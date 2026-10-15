"""Pricing research harness (not used in production).

Loads real awards from DATABASE_URL, builds the leakage-safe training frame, splits by time exactly like
production training (train < T-6m, calibrate T-6m..T-3m, test T-3m..T) and compares references and models,
stratified by how much NSN history each test row had.

    DATABASE_URL=postgresql+psycopg://... python ml/research/lab.py [--grid]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "ml")]

from dibbs_ml.features import PriorAwards, build_training_frame  # noqa: E402
from dibbs_ml.inflation import default_ppi  # noqa: E402

TIERS = [("cold (0)", 0, 0), ("1 award", 1, 1), ("2-4", 2, 4), ("5-19", 5, 19), ("20+", 20, 10**9)]


def load_awards() -> pd.DataFrame:
    from app.db import session_scope
    from app.services.training import load_awards_frame

    with session_scope() as db:
        return load_awards_frame(db)


def ape(pred: np.ndarray, price: np.ndarray) -> np.ndarray:
    return np.abs(pred - price) / price


def report(name: str, pred: np.ndarray, test: pd.DataFrame) -> dict:
    price = test["unit_price"].to_numpy(float)
    e = ape(pred, price)
    n_prior = test["n_prior_awards"].to_numpy(float)
    row = {"model": name, "n": len(e), "MdAPE": np.nanmedian(e), "within10%": np.nanmean(e <= 0.10),
           "within25%": np.nanmean(e <= 0.25)}
    for label, lo, hi in TIERS:
        m = (n_prior >= lo) & (n_prior <= hi)
        row[f"MdAPE {label}"] = np.nanmedian(e[m]) if m.any() else np.nan
        row[f"n {label}"] = int(m.sum())
    return row


def comps_reference(frame: pd.DataFrame, awards: pd.DataFrame, beta: float, tau: float, s: float,
                    agg: str = "wmedian") -> np.ndarray:
    """Recency- and quantity-similarity-weighted reference from the NSN's own prior awards.

    p_i' = p_i * ppi_i * (q / q_i)^beta;  w_i = exp(-age_i / tau) * exp(-|ln(q_i / q)| / s)
    """
    ppi = default_ppi()
    out = np.full(len(frame), np.nan)
    hist = {n: PriorAwards.from_frame(g) for n, g in awards[(awards.unit_price > 0) & (awards.quantity > 0)].groupby("nsn")}
    for i, r in enumerate(frame.itertuples(index=False)):
        h = hist.get(r.nsn)
        if h is None:
            continue
        p = h.before(r.as_of)
        if not len(p):
            continue
        d = p.dates.astype("datetime64[D]").astype(object)
        fac = np.array([ppi.factor(r.fsc, x, r.as_of) for x in d])
        q = max(float(r.quantity), 1e-9)
        lq = np.log(np.clip(p.qtys, 1e-9, None) / q)
        lp = np.log(np.clip(p.prices * fac, 1e-12, None)) - beta * lq  # (q/q_i)^beta == exp(-beta * ln(q_i/q))
        age = np.array([(r.as_of - x).days for x in d], float) / 365.25
        w = np.exp(-age / tau) * np.exp(-np.abs(lq) / s)
        if agg == "wmean":
            out[i] = math.exp(float(np.sum(w * lp) / np.sum(w)))
        else:
            o = np.argsort(lp)
            cw = np.cumsum(w[o]) / np.sum(w)
            out[i] = math.exp(float(lp[o][np.searchsorted(cw, 0.5)]))
    return out


def within_nsn_elasticity(train: pd.DataFrame) -> float:
    """Pooled within-NSN slope of log price on log quantity (training rows only)."""
    g = train.assign(lp=np.log(train.unit_price), lq=np.log(train.quantity))
    g = g.groupby("nsn").filter(lambda x: len(x) >= 2 and x.lq.nunique() >= 2)
    dp = g.lp - g.groupby("nsn").lp.transform("mean")
    dq = g.lq - g.groupby("nsn").lq.transform("mean")
    return float((dp * dq).sum() / (dq * dq).sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true", help="grid-search the comps reference on the train window")
    args = ap.parse_args()
    awards = load_awards()
    awards["fsc"] = awards["nsn"].str[:4]
    print(f"awards: {len(awards):,} rows, {awards.nsn.nunique():,} NSNs, sources ok")
    frame = build_training_frame(awards, default_ppi())
    as_of = pd.to_datetime(frame.award_date).max().date()
    from dibbs_ml.train import split_by_time

    train, calib, test, windows = split_by_time(frame, as_of)
    print(f"frame {len(frame):,} rows; train {len(train):,} calib {len(calib):,} test {len(test):,}; windows {windows}")
    beta = within_nsn_elasticity(train)
    print(f"within-NSN quantity elasticity (train): {beta:.3f}")

    rows = []
    warm = test[~test.is_cold]
    rows.append(report("last award (inflation-adj.)", warm.ref_price.to_numpy(float), warm))
    if args.grid:
        tr = train[~train.is_cold].sample(min(4000, int((~train.is_cold).sum())), random_state=1)
        best = None
        for b in (0.0, beta, beta * 0.5):
            for tau in (1.0, 2.0, 4.0, 1e9):
                for s in (0.5, 1.0, 2.0, 1e9):
                    for agg in ("wmedian", "wmean"):
                        e = np.nanmedian(ape(comps_reference(tr, awards, b, tau, s, agg), tr.unit_price.to_numpy(float)))
                        if best is None or e < best[0]:
                            best = (e, b, tau, s, agg)
        print(f"best comps on train: MdAPE {best[0]:.3f} beta {best[1]:.3f} tau {best[2]} s {best[3]} {best[4]}")
        _, b, tau, s, agg = best
    else:
        b, tau, s, agg = beta, 2.0, 1.0, "wmedian"
    comp = comps_reference(warm, awards, b, tau, s, agg)
    rows.append(report(f"comps ref (beta {b:.2f}, tau {tau}, s {s}, {agg})", comp, warm))

    from dibbs_ml.models import PricingModel

    model = PricingModel.train(train, calib)
    logq, _ = model.predict_log(test)
    med = np.exp(logq[:, list(model.levels).index(0.5)])
    rows.append(report("current model (all)", med, test))
    rows.append(report("current model (warm)", med[~test.is_cold.to_numpy()], warm))
    pd.set_option("display.width", 220, "display.max_columns", 30, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(rows).set_index("model").T)


if __name__ == "__main__" and not {"--cold", "--lno"} & set(sys.argv):
    main()


# ----------------------------------------------------------------------------- cold start: similar items
def load_item_meta() -> tuple[pd.DataFrame, pd.DataFrame]:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import ApprovedSource, SolicitationLine

    with session_scope() as db:
        lines = pd.DataFrame(db.execute(select(SolicitationLine.nsn, SolicitationLine.nomenclature,
                                               SolicitationLine.unit_of_issue)).all(),
                             columns=["nsn", "nomenclature", "unit_of_issue"])
        src = pd.DataFrame(db.execute(select(ApprovedSource.nsn, ApprovedSource.cage_code,
                                             ApprovedSource.part_number)).all(),
                           columns=["nsn", "cage_code", "part_number"])
    return lines, src


def add_similar(frame: pd.DataFrame, awards: pd.DataFrame, index, ppi) -> pd.DataFrame:
    from dibbs_ml.features import FscMedianIndex
    from dibbs_ml.items import histories_for, niin_country, similar_item_features

    hist = histories_for(awards)
    fidx = FscMedianIndex(awards.assign(fsc=awards.nsn.str[:4]))
    recs = []
    for r in frame.itertuples(index=False):
        feats, ref, _ = similar_item_features(index.neighbors(r.nsn), hist, r.quantity, r.as_of, ppi, r.fsc,
                                              fidx.get(r.fsc, r.as_of), items=index, nsn=r.nsn)
        recs.append({**feats, "sim_ref_price": ref if ref else np.nan, "niin_country": niin_country(r.nsn)})
    extra = pd.DataFrame(recs)
    base = frame.drop(columns=[c for c in extra.columns if c in frame.columns]).reset_index(drop=True)
    return pd.concat([base, extra], axis=1)


def cold_experiment() -> None:
    import lightgbm as lgb

    from dibbs_ml.features import COLD_FEATURES, to_model_frame
    from dibbs_ml.items import SIM_FEATURES, ItemIndex
    from dibbs_ml.models import LGBM_PARAMS, PricingModel, category_levels
    from dibbs_ml.train import split_by_time

    ppi = default_ppi()
    awards = load_awards()
    awards["fsc"] = awards["nsn"].str[:4]
    lines, src = load_item_meta()
    index = ItemIndex.from_frames(lines, src)
    print(f"item index: {len(index):,} NSNs described, {len(src):,} approved-source rows")
    frame = build_training_frame(awards, ppi)
    frame = add_similar(frame, awards, index, ppi)
    as_of = pd.to_datetime(frame.award_date).max().date()
    train, calib, test, _ = split_by_time(frame, as_of)
    cold = test[test.is_cold]
    has = cold.sim_ref_price.notna()
    print(f"test cold rows {len(cold)}; with similar-item evidence {int(has.sum())} ({has.mean():.0%})")

    rows = []
    model = PricingModel.train(train, calib)
    logq, _ = model.predict_log(cold)
    rows.append(report("current cold model (FSC median ref)", np.exp(logq[:, 5]), cold))
    rows.append(report("FSC median alone", cold.cold_ref_price.to_numpy(float), cold))
    rows.append(report("similar items alone (where found)", cold.sim_ref_price.to_numpy(float), cold))

    # v2 cold model: every training row re-expressed as if cold, reference = similar items (else FSC median).
    def cold2(df):
        c = df.copy()
        ref = np.where(c.sim_ref_price.notna(), c.sim_ref_price, c.cold_ref_price).astype(float)
        c["ref_type"] = np.where(c.sim_ref_price.notna(), "similar_items", c.cold_ref_type)
        c["log_ref_price"] = np.log(ref)
        c["y2"] = np.log(c.unit_price.astype(float) / ref)
        c["ref2"] = ref
        return c

    feats = COLD_FEATURES + SIM_FEATURES + ["niin_country"]
    tr = cold2(train)
    cats = category_levels(tr)
    cats["niin_country"] = sorted(set(tr.niin_country.astype(str)) | {"NA"})
    X = to_model_frame(tr, feats, cats)
    m = lgb.LGBMRegressor(objective="quantile", alpha=0.5, random_state=7, **LGBM_PARAMS).fit(X, tr.y2)
    te = cold2(cold)
    pred = te.ref2.to_numpy(float) * np.exp(m.predict(to_model_frame(te, feats, cats)))
    rows.append(report("v2 cold model (similar-items ref + features)", pred, cold))
    imp = sorted(zip(feats, m.booster_.feature_importance("gain"), strict=False), key=lambda t: -t[1])[:12]
    print("v2 importance:", [(f, round(float(v) / sum(x for _, x in imp), 3)) for f, v in imp])
    pd.set_option("display.width", 220, "display.max_columns", 30, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(rows).set_index("model")[["n", "MdAPE", "within10%", "within25%"]])


if __name__ == "__main__" and "--cold" in sys.argv:
    cold_experiment()


# ----------------------------------------------------------------------------- leave-NSN-out cold evaluation
RELIABLE = ("rfq_pdf_history", "award_pdf")


def load_awards_with_source() -> pd.DataFrame:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import AwardHistory

    with session_scope() as db:
        return pd.DataFrame(db.execute(select(AwardHistory.nsn, AwardHistory.award_date, AwardHistory.unit_price,
                                              AwardHistory.quantity, AwardHistory.awardee_cage,
                                              AwardHistory.contract_number, AwardHistory.source)).all(),
                            columns=["nsn", "award_date", "unit_price", "quantity", "awardee_cage", "contract_number",
                                     "source"])


def cold_lno(holdout_share: float = 0.25, seed: int = 3) -> None:
    """New-part accuracy: whole NSNs are held out and priced only from OTHER NSNs (reliable sources only)."""
    import lightgbm as lgb

    from dibbs_ml.features import COLD_FEATURES, to_model_frame
    from dibbs_ml.items import SIM_FEATURES, ItemIndex
    from dibbs_ml.models import LGBM_PARAMS, category_levels, cold_frame

    ppi = default_ppi()
    aw = load_awards_with_source()
    aw = aw[aw.source.isin(RELIABLE)].copy()
    aw["fsc"] = aw.nsn.str[:4]
    lines, src = load_item_meta()
    index = ItemIndex.from_frames(lines, src)
    frame = add_similar(build_training_frame(aw, ppi), aw, index, ppi)
    rng = np.random.default_rng(seed)
    nsns = np.array(sorted(frame.nsn.unique()))
    hold = set(rng.choice(nsns, int(len(nsns) * holdout_share), replace=False))
    cutoff = pd.Timestamp(pd.to_datetime(frame.award_date).max()) - pd.DateOffset(years=2)
    test = frame[frame.nsn.isin(hold) & (pd.to_datetime(frame.award_date) >= cutoff)].copy()
    train = frame[~frame.nsn.isin(hold)].copy()
    print(f"reliable awards {len(aw):,}; NSNs {len(nsns)}; holdout NSNs {len(hold)}; test rows {len(test)} "
          f"(similar-item evidence on {test.sim_ref_price.notna().mean():.0%}); train rows {len(train)}")

    rows = [report("FSC median", test.cold_ref_price.to_numpy(float), test.assign(n_prior_awards=0)),
            report("similar items alone", test.sim_ref_price.to_numpy(float), test.assign(n_prior_awards=0))]
    # current cold model: all training rows re-expressed relative to the FSC median, no history features
    ct = cold_frame(train)
    cats = category_levels(ct)
    cats["niin_country"] = sorted(set(frame.niin_country.astype(str)) | {"NA"})
    m1 = lgb.LGBMRegressor(objective="quantile", alpha=0.5, random_state=7, **LGBM_PARAMS).fit(
        to_model_frame(ct, COLD_FEATURES, cats), ct.y_cold)
    te = cold_frame(test)
    p1 = te.cold_ref_price.to_numpy(float) * np.exp(m1.predict(to_model_frame(te, COLD_FEATURES, cats)))
    rows.append(report("current cold model", p1, test.assign(n_prior_awards=0)))

    def v2(df):
        c = cold_frame(df)
        ref = np.where(c.sim_ref_price.notna(), c.sim_ref_price, c.cold_ref_price).astype(float)
        c["log_ref_price"], c["ref2"] = np.log(ref), ref
        c["ref_type"] = np.where(c.sim_ref_price.notna(), "similar_items", c.cold_ref_type)
        c["y2"] = np.log(c.unit_price.astype(float) / ref)
        return c

    feats = COLD_FEATURES + SIM_FEATURES + ["niin_country"]
    t2 = v2(train)
    cats2 = category_levels(t2)
    cats2["niin_country"] = cats["niin_country"]
    m2 = lgb.LGBMRegressor(objective="quantile", alpha=0.5, random_state=7, **LGBM_PARAMS).fit(
        to_model_frame(t2, feats, cats2), t2.y2)
    e2 = v2(test)
    p2 = e2.ref2.to_numpy(float) * np.exp(m2.predict(to_model_frame(e2, feats, cats2)))
    rows.append(report("v2: similar-items ref + features", p2, test.assign(n_prior_awards=0)))
    has = test.sim_ref_price.notna().to_numpy()
    rows.append(report("  v2 where similar items found", p2[has], test[has].assign(n_prior_awards=0)))
    rows.append(report("  current where similar items found", p1[has], test[has].assign(n_prior_awards=0)))
    pd.set_option("display.width", 220, "display.max_columns", 30, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(rows).set_index("model")[["n", "MdAPE", "within10%", "within25%"]])


if __name__ == "__main__" and "--lno" in sys.argv:
    cold_lno()
