"""Training orchestration (DataFrame in, artifact + metrics out). DB access lives in the backend."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dibbs_ml.backtest import run_backtest
from dibbs_ml.cdf import QUANTILE_LEVELS
from dibbs_ml.evaluate import pwin_calibration, quantile_metrics
from dibbs_ml.features import build_training_frame
from dibbs_ml.inflation import PPI, default_ppi
from dibbs_ml.models import PricingModel, time_windows

MIN_WINDOW_ROWS = 30


@dataclass
class TrainResult:
    model: PricingModel
    metrics: dict
    train_window: str
    test_window: str
    frame: pd.DataFrame


def split_by_time(frame: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    w = time_windows(as_of)
    d = pd.to_datetime(frame["award_date"]).dt.date
    parts = {k: frame[(d >= lo) & (d < hi if k != "test" else d <= hi)] for k, (lo, hi) in w.items()}
    windows = {k: (str(lo if k != "train" else d.min()), str(hi)) for k, (lo, hi) in w.items()}
    if min(len(p) for p in parts.values()) < MIN_WINDOW_ROWS:
        # Not enough recent data for calendar windows: fall back to 60/20/20 by time order.
        order = frame.sort_values("award_date")
        n = len(order)
        a, b = int(n * 0.6), int(n * 0.8)
        parts = {"train": order.iloc[:a], "calib": order.iloc[a:b], "test": order.iloc[b:]}
        windows = {
            k: (str(p["award_date"].min()), str(p["award_date"].max())) for k, p in parts.items() if len(p)
        }
        windows["fallback"] = "time-ordered 60/20/20 split (calendar windows had < 30 rows)"
    return parts["train"], parts["calib"], parts["test"], windows


# Accuracy is reported by how much award history the NSN had: that is what a broker can expect per line.
HISTORY_TIERS = [("no history", 0, 0), ("1 award", 1, 1), ("2-4 awards", 2, 4), ("5+ awards", 5, 10**9)]


def evaluate_model(model: PricingModel, test: pd.DataFrame, kind: str = "model") -> tuple[dict, np.ndarray]:
    logq, is_cold = model.predict_log(test, kind=kind)
    price = test["unit_price"].to_numpy(dtype=float)
    m = quantile_metrics(logq, price, model.levels)

    def flat(mask):
        return {k: v for k, v in quantile_metrics(logq[mask], price[mask], model.levels).items() if not isinstance(v, dict)}

    if is_cold.any() and (~is_cold).any():
        m["warm"] = flat(~is_cold)
        m["cold"] = flat(is_cold)
    n_prior = test["n_prior_awards"].to_numpy(dtype=float) if "n_prior_awards" in test else np.zeros(len(test))
    m["by_history"] = {
        label: flat((n_prior >= lo) & (n_prior <= hi)) for label, lo, hi in HISTORY_TIERS
        if ((n_prior >= lo) & (n_prior <= hi)).sum() >= 5
    }
    return m, logq


def train_from_awards(
    awards: pd.DataFrame,
    as_of: date,
    ppi: PPI | None = None,
    active_model: PricingModel | None = None,
    items=None,
) -> TrainResult:
    ppi = ppi or default_ppi()
    frame = build_training_frame(awards, ppi, items=items)
    if len(frame) < 3 * MIN_WINDOW_ROWS:
        raise ValueError(f"not enough training rows ({len(frame)}); run the backfill or seed first")
    train, calib, test, windows = split_by_time(frame, as_of)
    model = PricingModel.train(train, calib, QUANTILE_LEVELS)
    model.trained_at = datetime.now(UTC).isoformat()
    model.train_window = f"{windows['train'][0]}..{windows['train'][1]}"
    model.calib_window = f"{windows['calib'][0]}..{windows['calib'][1]}"

    main_m, logq = evaluate_model(model, test, "model")
    raw_m, _ = evaluate_model(model, test, "uncalibrated")
    base_m, base_logq = evaluate_model(model, test, "baseline")
    from dibbs_ml.models import cold_frame

    cold_ref = cold_frame(test, model.cold_ref_mode)["cold_ref_used"].to_numpy(dtype=float)
    ref = np.where(test["is_cold"], cold_ref, test["ref_price"]).astype(float)
    calib_plot = pwin_calibration(logq, test["unit_price"].to_numpy(dtype=float), ref, model.levels)
    base_calib = pwin_calibration(base_logq, test["unit_price"].to_numpy(dtype=float), ref, model.levels)
    backtest = run_backtest(test, logq, model.levels)

    metrics = {
        "primary_metric": "pinball_mean",
        "pinball_mean": main_m["pinball_mean"],
        "model": main_m,
        "uncalibrated": raw_m,
        "baseline": base_m,
        "pwin_calibration": calib_plot,
        "baseline_pwin_calibration": base_calib,
        "backtest": backtest,
        "rows": {"train": len(train), "calib": len(calib), "test": len(test), "total": len(frame)},
        "cold_share_test": float(test["is_cold"].mean()),
        "windows": windows,
        "feature_importance": dict(list(model.warm.feature_importance().items())[:15]),
        "conformal_deltas": {"warm": model.warm_deltas.tolist(), "cold": model.cold_deltas.tolist()},
    }
    if active_model is not None:
        try:
            act_m, _ = evaluate_model(active_model, test, "model")
            metrics["active_model_on_same_test"] = {
                k: act_m[k] for k in ("pinball_mean", "mape_median", "median_abs_pct_error", "coverage_80",
                                      "coverage_90", "n", "warm", "cold", "by_history") if k in act_m
            }
        except Exception as exc:  # incompatible old artifact
            metrics["active_model_on_same_test"] = {"error": str(exc)[:300]}
    return TrainResult(
        model=model,
        metrics=json.loads(json.dumps(metrics, default=float)),
        train_window=model.train_window,
        test_window=f"{windows['test'][0]}..{windows['test'][1]}",
        frame=frame,
    )


def should_promote(metrics: dict) -> tuple[bool, str]:
    act = metrics.get("active_model_on_same_test")
    if not act or "pinball_mean" not in act:
        return True, "no comparable active model"
    new, old = metrics["pinball_mean"], act["pinball_mean"]
    if new < old:
        return True, f"pinball {new:.4f} < active {old:.4f}"
    return False, f"pinball {new:.4f} >= active {old:.4f}"


def save_artifact(model: PricingModel, directory: Path, run_id: int | str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"model_run_{run_id}.joblib"
    model.save(path)
    return path
