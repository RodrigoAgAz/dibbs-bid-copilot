"""Pricing research that runs next to the production database (worker, bulk queue) and logs its results.

The production database is not reachable from outside Railway, and exposing it is not worth the risk, so
experiments run in-cluster: set RESEARCH_RUN=<id> on the worker service and it runs once (marker per id); the
results are logged as "RESEARCH <id> <experiment>: {...}" lines (railway logs -s worker). Each experiment uses the
quality-filtered awards and the production time split; nothing here changes the active model.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import date

import lightgbm as lgb
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from dibbs_ml.cdf import QUANTILE_LEVELS
from dibbs_ml.features import COLD_FEATURES, build_training_frame, to_model_frame
from dibbs_ml.inflation import default_ppi
from dibbs_ml.models import LGBM_PARAMS, PricingModel, category_levels, cold_frame, pinball

log = logging.getLogger(__name__)
LV = list(QUANTILE_LEVELS)
J50 = LV.index(0.5)
TIERS = [("none", 0, 0), ("1", 1, 1), ("2-4", 2, 4), ("5+", 5, 10**9)]


def _score(logq: np.ndarray, df: pd.DataFrame) -> dict:
    y = np.log(df["unit_price"].to_numpy(float))
    price = np.exp(y)
    med = np.exp(logq[:, J50])
    ape = np.abs(med - price) / price
    out = {
        "n": int(len(y)),
        "MdAPE": round(float(np.median(ape)), 4) if len(y) else None,
        "within10": round(float(np.mean(ape <= 0.10)), 3) if len(y) else None,
        "pinball": round(float(np.mean([pinball(y, logq[:, k], a) for k, a in enumerate(LV)])), 4) if len(y) else None,
        "cov80": round(float(np.mean((y >= logq[:, LV.index(0.1)]) & (y <= logq[:, LV.index(0.9)]))), 3) if len(y) else None,
    }
    n_prior = df["n_prior_awards"].to_numpy(float)
    for label, lo, hi in TIERS:
        m = (n_prior >= lo) & (n_prior <= hi)
        if m.sum() >= 20:
            out[f"MdAPE[{label}]"] = round(float(np.median(ape[m])), 4)
            out[f"cov80[{label}]"] = round(float(np.mean((y[m] >= logq[m, LV.index(0.1)]) & (y[m] <= logq[m, LV.index(0.9)]))), 3)
    return out


def _windows(frame: pd.DataFrame, as_of: date, calib_months: float) -> tuple:
    """Test: the last 3 months. Calibration: the `calib_months` before that. Train: everything earlier."""
    d = pd.to_datetime(frame["award_date"])
    t = pd.Timestamp(as_of)
    test_start = t - pd.DateOffset(months=3)
    calib_start = test_start - pd.DateOffset(days=int(calib_months * 30.4))
    train = frame[d < calib_start]
    calib = frame[(d >= calib_start) & (d < test_start)]
    test = frame[d >= test_start]
    return train, calib, test


def _log(run_id: str, name: str, payload: dict) -> None:
    log.info("RESEARCH %s %s: %s", run_id, name, json.dumps(payload, default=str))


def run_research(db: Session, run_id: str, experiments: list[str] | None = None) -> dict:
    from app.services.item_index import build_item_index
    from app.services.training import load_awards_frame

    t0 = time.monotonic()
    experiments = experiments or ["recency", "shrink", "capacity", "cold"]
    awards = load_awards_frame(db)
    awards["fsc"] = awards["nsn"].str[:4]
    items = build_item_index(db)
    ppi = default_ppi()
    frame = build_training_frame(awards, ppi, items=items)
    as_of = pd.to_datetime(frame["award_date"]).max().date()
    _log(run_id, "data", {"awards": len(awards), "rows": len(frame), "nsns": int(frame.nsn.nunique()),
                          "as_of": as_of, "quality": awards.attrs.get("quality"),
                          "seconds": round(time.monotonic() - t0)})
    results: dict = {}

    if "recency" in experiments:
        # Same test window (last 3 months); train on data ending 6 / 4 / 3.5 months before T.
        for name, calib_m in (("A: train<T-6m, calib 3m (production today)", 3.0), ("B: train<T-4m, calib 1m", 1.0),
                              ("C: train<T-3.5m, calib 0.5m", 0.5)):
            train, calib, test = _windows(frame, as_of, calib_m)
            m = PricingModel.train(train, calib)
            logq, _ = m.predict_log(test)
            results[name] = {**_score(logq, test), "train_rows": len(train), "calib_rows": len(calib)}
            _log(run_id, f"recency {name}", results[name])
        base_train, base_calib, test = _windows(frame, as_of, 3.0)
        mb = PricingModel.train(base_train, base_calib)
        lb, _ = mb.predict_log(test, kind="baseline")
        results["baseline (last award)"] = _score(lb, test)
        _log(run_id, "recency baseline", results["baseline (last award)"])

    if "shrink" in experiments:
        train, calib, test = _windows(frame, as_of, 3.0)
        m = PricingModel.train(train, calib)
        warm_c, warm_t = calib[~calib.is_cold], test[~test.is_cold]
        lc, _ = m.predict_log(warm_c)
        lt, _ = m.predict_log(warm_t)
        rc, rt = np.log(warm_c.ref_price.to_numpy(float)), np.log(warm_t.ref_price.to_numpy(float))
        yc = warm_c.unit_price.to_numpy(float)

        def shrink(lq, lref, lam):
            return lq - ((1 - lam) * (lq[:, J50] - lref))[:, None]

        lam = min(np.linspace(0, 1, 11), key=lambda x: np.median(np.abs(np.exp(shrink(lc, rc, x)[:, J50]) - yc) / yc))
        results["shrink"] = {"lambda": float(lam), "model": _score(lt, warm_t), "shrunk": _score(shrink(lt, rt, lam), warm_t)}
        _log(run_id, "shrink", results["shrink"])

    if "capacity" in experiments:
        train, calib, test = _windows(frame, as_of, 3.0)
        warm_tr, warm_t = train[~train.is_cold], test[~test.is_cold]
        from dibbs_ml.features import FEATURES

        cats = category_levels(train)
        Xtr, Xt = to_model_frame(warm_tr, FEATURES, cats), to_model_frame(warm_t, FEATURES, cats)
        lref = np.log(warm_t.ref_price.to_numpy(float))
        for cfg in ({"num_leaves": 15, "n_estimators": 350, "min_child_samples": 25},
                    {"num_leaves": 31, "n_estimators": 600, "min_child_samples": 25},
                    {"num_leaves": 63, "n_estimators": 900, "min_child_samples": 20, "learning_rate": 0.03},
                    {"num_leaves": 15, "n_estimators": 350, "min_child_samples": 100}):
            params = {**LGBM_PARAMS, **cfg}
            P = np.column_stack([
                lgb.LGBMRegressor(objective="quantile", alpha=a, random_state=7, **params).fit(Xtr, warm_tr.y).predict(Xt)
                for a in LV])
            logq = np.sort(P, axis=1) + lref[:, None]
            key = "capacity " + ",".join(f"{k}={v}" for k, v in cfg.items())
            results[key] = _score(logq, warm_t)  # uncalibrated: compare pinball/MdAPE across configs
            _log(run_id, key, results[key])

    if "cold" in experiments:
        # Whole NSNs held out (verified prices only), priced only from OTHER NSNs.
        ver = frame.copy()
        rng = np.random.default_rng(3)
        nsns = np.array(sorted(ver.nsn.unique()))
        hold = set(rng.choice(nsns, int(len(nsns) * 0.2), replace=False))
        cutoff = pd.Timestamp(as_of) - pd.DateOffset(years=2)
        test = ver[ver.nsn.isin(hold) & (pd.to_datetime(ver.award_date) >= cutoff)].assign(n_prior_awards=0.0)
        train = ver[~ver.nsn.isin(hold)]
        out = {"test_rows": len(test), "with_family_evidence": float(test.sim_ref_price.notna().mean())}
        for mode in ("fsc", "similar"):
            ct = cold_frame(train, mode)
            cats = category_levels(ct)
            mdl = lgb.LGBMRegressor(objective="quantile", alpha=0.5, random_state=7, **LGBM_PARAMS).fit(
                to_model_frame(ct, COLD_FEATURES, cats), ct.y_cold)
            te = cold_frame(test, mode)
            pred = te.cold_ref_used.to_numpy(float) * np.exp(mdl.predict(to_model_frame(te, COLD_FEATURES, cats)))
            ape = np.abs(pred - test.unit_price.to_numpy(float)) / test.unit_price.to_numpy(float)
            has = test.sim_ref_price.notna().to_numpy()
            out[mode] = {"MdAPE": round(float(np.median(ape)), 4),
                         "MdAPE_with_evidence": round(float(np.median(ape[has])), 4) if has.sum() else None,
                         "within25": round(float(np.mean(ape <= 0.25)), 3)}
        fe = test[test.sim_ref_price.notna()]
        if len(fe):
            e = np.abs(fe.sim_ref_price - fe.unit_price) / fe.unit_price
            out["family_evidence_alone_MdAPE"] = round(float(np.median(e)), 4)
        results["cold"] = out
        _log(run_id, "cold", out)

    results["seconds"] = round(time.monotonic() - t0)
    _log(run_id, "done", {"seconds": results["seconds"]})
    return json.loads(json.dumps(results, default=lambda v: None if isinstance(v, float) and math.isnan(v) else str(v)))
