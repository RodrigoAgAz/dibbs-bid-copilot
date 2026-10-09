"""Evaluation metrics on a held-out time window."""

from __future__ import annotations

import numpy as np

from dibbs_ml.cdf import QUANTILE_LEVELS, PriceDistribution
from dibbs_ml.models import pinball

N_BID_SAMPLES = 5


def quantile_metrics(logq: np.ndarray, price: np.ndarray, levels=QUANTILE_LEVELS) -> dict:
    """Pinball loss (log-price space; primary), MAPE of the median, interval coverage."""
    y = np.log(np.asarray(price, dtype=float))
    lv = list(levels)
    per_q = {f"{a:.2f}": pinball(y, logq[:, j], a) for j, a in enumerate(lv)}
    med = np.exp(logq[:, lv.index(0.5)])
    out = {
        "n": int(len(y)),
        "pinball_mean": float(np.mean(list(per_q.values()))),
        "pinball_per_quantile": per_q,
        "mape_median": float(np.mean(np.abs(med - price) / price)),
        "median_abs_pct_error": float(np.median(np.abs(med - price) / price)),
        "coverage_80": float(np.mean((y >= logq[:, lv.index(0.1)]) & (y <= logq[:, lv.index(0.9)]))),
        "coverage_90": float(np.mean((y >= logq[:, lv.index(0.05)]) & (y <= logq[:, lv.index(0.95)]))),
        "quantile_coverage": {f"{a:.2f}": float(np.mean(y <= logq[:, j])) for j, a in enumerate(lv)},
    }
    return out


def pwin_calibration(
    logq: np.ndarray,
    price: np.ndarray,
    ref_price: np.ndarray,
    levels=QUANTILE_LEVELS,
    seed: int = 11,
    n_bins: int = 10,
) -> dict:
    """Bucket predicted P(W > b) at sampled bids b and compare with the observed frequency.

    Bids are sampled around the reference price, b = ref * exp(N(0, 0.25)), independent of the model,
    so the check covers realistic bids rather than the model's own quantiles.
    """
    rng = np.random.default_rng(seed)
    preds, obs = [], []
    for i in range(len(price)):
        dist = PriceDistribution(levels, np.exp(logq[i]))
        bids = ref_price[i] * np.exp(rng.normal(0.0, 0.25, N_BID_SAMPLES))
        p = np.asarray(dist.sf(bids)).reshape(-1)
        preds.extend(p.tolist())
        obs.extend((price[i] > bids).astype(float).tolist())
    preds, obs = np.array(preds), np.array(obs)
    edges = np.linspace(0, 1, n_bins + 1)
    bins = []
    for k in range(n_bins):
        m = (preds >= edges[k]) & (preds < edges[k + 1] if k < n_bins - 1 else preds <= edges[k + 1])
        if m.sum():
            bins.append(
                {
                    "bin": f"{edges[k]:.1f}-{edges[k + 1]:.1f}",
                    "predicted": float(preds[m].mean()),
                    "observed": float(obs[m].mean()),
                    "count": int(m.sum()),
                }
            )
    ece = float(sum(b["count"] * abs(b["predicted"] - b["observed"]) for b in bins) / max(len(preds), 1))
    return {"bins": bins, "brier": float(np.mean((preds - obs) ** 2)) if len(preds) else None, "ece": ece}
