"""Pricing models: baseline, LightGBM quantile regression, split-conformal calibration, cold start.

All models predict quantiles of y = log(W / reference_price) and are turned into prices by
multiplying by the reference. The serialized `PricingModel` is the artifact stored per model run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from dibbs_ml.cdf import QUANTILE_LEVELS
from dibbs_ml.features import CAT_FEATURES, COLD_FEATURES, FEATURES, to_model_frame

LOW_DATA_PRIOR_AWARDS = 3  # fewer prior awards than this -> "low data" badge
COLD_WIDEN = 1.25  # extra widening of the cold-start interval after conformal calibration
MIN_GROUP_CALIBRATION = 40

LGBM_PARAMS = dict(
    n_estimators=350,
    learning_rate=0.04,
    num_leaves=15,
    min_child_samples=25,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.85,
    reg_lambda=1.0,
    verbose=-1,
)


def pinball(y: np.ndarray, q: np.ndarray, alpha: float) -> float:
    d = y - q
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


def category_levels(df: pd.DataFrame) -> dict[str, list[str]]:
    cats = {}
    for c in CAT_FEATURES:
        vals = sorted({str(v) for v in df[c].dropna().astype(str)} | {"NA"})
        cats[c] = vals
    return cats


@dataclass
class QuantileGBM:
    features: list[str]
    categories: dict[str, list[str]]
    levels: tuple[float, ...] = QUANTILE_LEVELS
    models: dict[float, lgb.LGBMRegressor] = field(default_factory=dict)
    seed: int = 7

    def fit(self, df: pd.DataFrame, y: np.ndarray) -> QuantileGBM:
        X = to_model_frame(df, self.features, self.categories)
        for a in self.levels:
            m = lgb.LGBMRegressor(objective="quantile", alpha=a, random_state=self.seed, **LGBM_PARAMS)
            m.fit(X, y)
            self.models[a] = m
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        X = to_model_frame(df, self.features, self.categories)
        P = np.column_stack([self.models[a].predict(X) for a in self.levels])
        return np.sort(P, axis=1)  # monotone quantiles

    def contributions(self, df: pd.DataFrame) -> np.ndarray:
        """Per-feature contributions (log-ratio units) of the median model; last column = bias."""
        X = to_model_frame(df, self.features, self.categories)
        return self.models[0.5].predict(X, pred_contrib=True)

    def feature_importance(self) -> dict[str, float]:
        m = self.models[0.5]
        imp = m.booster_.feature_importance(importance_type="gain")
        tot = float(imp.sum()) or 1.0
        return {f: float(v) / tot for f, v in sorted(zip(self.features, imp, strict=True), key=lambda t: -t[1])}


@dataclass
class Baseline:
    """W ~ reference price (inflation-adjusted last award, else DoD/FSC fallback) x empirical residuals per FSC."""

    levels: tuple[float, ...] = QUANTILE_LEVELS
    by_fsc: dict[str, list[float]] = field(default_factory=dict)
    pooled: list[float] = field(default_factory=list)
    min_rows: int = 30

    def fit(self, df: pd.DataFrame, y: np.ndarray) -> Baseline:
        y = np.asarray(y, dtype=float)
        self.pooled = [float(v) for v in np.quantile(y, self.levels)]
        for fsc, idx in pd.Series(range(len(df)), index=df["fsc"].astype(str).values).groupby(level=0):
            if len(idx) >= self.min_rows:
                self.by_fsc[str(fsc)] = [float(v) for v in np.quantile(y[idx.values], self.levels)]
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return np.array([self.by_fsc.get(str(f), self.pooled) for f in df["fsc"].astype(str)], dtype=float)


def fit_conformal(pred: np.ndarray, y: np.ndarray, levels=QUANTILE_LEVELS) -> np.ndarray:
    """Split-conformal shift per quantile so empirical P(y <= q_a + d_a) = a on the calibration set."""
    y = np.asarray(y, dtype=float)
    if len(y) == 0:
        return np.zeros(len(levels))
    resid = y[:, None] - pred
    n = len(y)
    deltas = []
    for j, a in enumerate(levels):
        # finite-sample adjusted level, clipped to (0, 1)
        lvl = min(max(math.ceil((n + 1) * a) / n if a >= 0.5 else math.floor((n + 1) * a) / n, 0.0), 1.0)
        deltas.append(float(np.quantile(resid[:, j], lvl)))
    return np.asarray(deltas)


def apply_conformal(pred: np.ndarray, deltas: np.ndarray) -> np.ndarray:
    return np.sort(pred + deltas[None, :], axis=1)


def widen(pred: np.ndarray, factor: float, levels=QUANTILE_LEVELS) -> np.ndarray:
    mid = pred[:, list(levels).index(0.5)][:, None]
    return mid + factor * (pred - mid)


def cold_frame(df: pd.DataFrame, mode: str = "fsc") -> pd.DataFrame:
    """Re-express rows relative to the cold-start (no NSN history) reference.

    mode "fsc" (v1): DoD price, else the FSC median. mode "similar" (v2): similar-item evidence (same
    part-number family / manufacturer) when there is any, else the v1 reference. `cold_ref_used` is the
    reference the cold model's log-ratio predictions are relative to; `y_cold` is recomputed against it.
    """
    c = df.copy()
    ref = c["cold_ref_price"].astype(float).to_numpy()
    rtype = c["cold_ref_type"].astype(object).to_numpy()
    if mode == "similar" and "sim_ref_price" in c:
        s = pd.to_numeric(c["sim_ref_price"], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(s) & (s > 0)
        ref, rtype = np.where(ok, s, ref), np.where(ok, "similar_items", rtype)
    c["ref_type"] = rtype
    c["cold_ref_used"] = ref
    c["log_ref_price"] = np.log(ref)
    if "unit_price" in c:
        c["y_cold"] = np.log(c["unit_price"].astype(float).to_numpy() / ref)
    return c


@dataclass
class PricingModel:
    """Serialized model artifact."""

    levels: tuple[float, ...]
    warm: QuantileGBM
    cold: QuantileGBM
    baseline: Baseline
    cold_baseline: Baseline
    warm_deltas: np.ndarray
    cold_deltas: np.ndarray
    cold_widen: float = COLD_WIDEN
    cold_ref_mode: str = "fsc"  # v1 artifacts have no such attribute and fall back to this class default
    # The deployed model is refit on (nearly) all data; this held-out twin (trained before the test window)
    # is what later candidates are compared against, so the comparison never favors a model that saw the test.
    eval_model: PricingModel | None = None
    # New parts with part-number-family evidence: the evidence itself is the median (production, held-out NSNs:
    # 64% median error vs 88% for the cold model and 91% for the model adjusted on top of the evidence); the
    # model only supplies the spread. Older artifacts default to False.
    anchor_family_evidence: bool = False
    trained_at: str = ""
    train_window: str = ""
    calib_window: str = ""
    model_version: str = "lgbm_quantile_v1"

    # ------------------------------------------------------------ training
    @classmethod
    def train(cls, train_df: pd.DataFrame, calib_df: pd.DataFrame, levels=QUANTILE_LEVELS,
              cold_ref_mode: str = "similar") -> PricingModel:
        cats = category_levels(pd.concat([train_df, cold_frame(train_df, cold_ref_mode)]))
        warm_train = train_df[~train_df["is_cold"]]
        warm = QuantileGBM(FEATURES, cats, levels).fit(warm_train, warm_train["y"].to_numpy())
        ctrain = cold_frame(train_df, cold_ref_mode)
        cold = QuantileGBM(COLD_FEATURES, cats, levels).fit(ctrain, ctrain["y_cold"].to_numpy())
        baseline = Baseline(levels).fit(warm_train, warm_train["y"].to_numpy())
        cold_baseline = Baseline(levels).fit(ctrain, ctrain["y_cold"].to_numpy())

        model = cls(
            levels=tuple(levels),
            warm=warm,
            cold=cold,
            baseline=baseline,
            cold_baseline=cold_baseline,
            warm_deltas=np.zeros(len(levels)),
            cold_deltas=np.zeros(len(levels)),
            cold_ref_mode=cold_ref_mode,
            anchor_family_evidence=cold_ref_mode == "similar",
            model_version="lgbm_quantile_v2" if cold_ref_mode == "similar" else "lgbm_quantile_v1",
        )
        # Group-conditional split conformal on the calibration window.
        cw = calib_df[~calib_df["is_cold"]]
        if len(cw):
            model.warm_deltas = fit_conformal(warm.predict(cw), cw["y"].to_numpy(), levels)
        cc = calib_df[calib_df["is_cold"]]
        if len(cc) < MIN_GROUP_CALIBRATION:
            cc = cold_frame(calib_df, cold_ref_mode)  # not enough true cold rows: calibrate on all rows
            ycc = cc["y_cold"].to_numpy()
        else:
            cc = cold_frame(cc, cold_ref_mode)
            ycc = cc["y_cold"].to_numpy()
        if len(cc):
            model.cold_deltas = fit_conformal(cold.predict(cc), ycc, levels)
        return model

    # ------------------------------------------------------------ inference
    def predict_log(self, df: pd.DataFrame, kind: str = "model") -> tuple[np.ndarray, np.ndarray]:
        """Log-price quantiles (n, L) and the cold-start mask. kind: model | baseline | uncalibrated."""
        n = len(df)
        out = np.zeros((n, len(self.levels)))
        is_cold = df["is_cold"].to_numpy(dtype=bool) if "is_cold" in df else (df["n_prior_awards"] == 0).to_numpy()
        warm_idx, cold_idx = np.where(~is_cold)[0], np.where(is_cold)[0]
        if warm_idx.size:
            w = df.iloc[warm_idx]
            if kind == "baseline":
                q = self.baseline.predict(w)
            else:
                q = self.warm.predict(w)
                if kind == "model":
                    q = apply_conformal(q, self.warm_deltas)
            out[warm_idx] = np.log(w["ref_price"].to_numpy(dtype=float))[:, None] + q
        if cold_idx.size:
            c = cold_frame(df.iloc[cold_idx], self.cold_ref_mode)
            if kind == "baseline":
                q = self.cold_baseline.predict(c)
            else:
                q = self.cold.predict(c)
                if kind == "model":
                    q = widen(apply_conformal(q, self.cold_deltas), self.cold_widen, self.levels)
                if self.anchor_family_evidence and "sim_ref_price" in c:
                    has = np.isfinite(pd.to_numeric(c["sim_ref_price"], errors="coerce").to_numpy(dtype=float))
                    mid = list(self.levels).index(0.5)
                    q[has] = q[has] - q[has, mid][:, None]  # median = the evidence (reference); keep the spread
            out[cold_idx] = np.log(c["cold_ref_used"].to_numpy(dtype=float))[:, None] + q
        return np.sort(out, axis=1), is_cold

    def explain(self, row: pd.DataFrame, top: int = 6) -> list[dict]:
        """Top feature contributions to the median prediction (log-ratio units -> % effect)."""
        is_cold = bool(row["is_cold"].iloc[0]) if "is_cold" in row else bool(row["n_prior_awards"].iloc[0] == 0)
        model = self.cold if is_cold else self.warm
        frame = cold_frame(row, self.cold_ref_mode) if is_cold else row
        contrib = model.contributions(frame)[0]
        feats = model.features
        pairs = sorted(zip(feats, contrib[:-1], strict=True), key=lambda t: -abs(t[1]))[:top]
        out = []
        for f, v in pairs:
            val = frame[f].iloc[0]
            out.append(
                {
                    "feature": f,
                    "value": None if (isinstance(val, float) and math.isnan(val)) else (val if isinstance(val, str) else float(val)),
                    "contribution": float(v),
                    "effect_pct": float(math.expm1(v) * 100),
                }
            )
        return out

    def save(self, path) -> None:
        joblib.dump(self, path)

    @staticmethod
    def load(path) -> PricingModel:
        return joblib.load(path)


def time_windows(as_of: date) -> dict[str, tuple[date, date]]:
    """Train: < T-4m. Calibrate: [T-4m, T-3m). Test: [T-3m, T].

    A one-month calibration window right before the test (instead of three months, with training ending at
    T-6m) gave better pinball loss (0.209 vs 0.212) and honest coverage (80% interval: 84% vs 78%) on production
    data: prices drift, and recent residuals calibrate better (app/services/research.py, 2026-10-15).
    """
    t = pd.Timestamp(as_of)
    t4 = (t - pd.DateOffset(months=4)).date()
    t3 = (t - pd.DateOffset(months=3)).date()
    return {"train": (date(1900, 1, 1), t4), "calib": (t4, t3), "test": (t3, as_of)}
