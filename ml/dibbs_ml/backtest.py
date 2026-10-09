"""Backtest of the bidding policy on test-period awards.

Assumption (documented in the report): a broker's landed cost for a line is the actual winning
price times U(0.6, 0.95). A bid b wins when b < W (lowest acceptable price wins; ties lose).
Profit of a won bid = (b - cost) * quantity. Lost bids earn 0.

Policies:
  * model:            the recommendation engine (argmax expected profit), skipping "no bid" lines
  * last_minus_2pct:  bid the last award price for the NSN minus 2% (skip if no prior award)
  * cost_plus_15pct:  bid landed cost + 15%
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dibbs_ml.bidding import recommend_bid
from dibbs_ml.cdf import QUANTILE_LEVELS, PriceDistribution

COST_LOW, COST_HIGH = 0.60, 0.95


def run_backtest(
    test_df: pd.DataFrame,
    logq: np.ndarray,
    levels=QUANTILE_LEVELS,
    min_margin_pct: float = 0.05,
    min_expected_profit: float = 0.0,
    seed: int = 42,
) -> dict:
    rng = np.random.default_rng(seed)
    W = test_df["unit_price"].to_numpy(dtype=float)
    qty = test_df["quantity"].to_numpy(dtype=float)
    cost = W * rng.uniform(COST_LOW, COST_HIGH, len(W))
    last = test_df["last_price_raw"].to_numpy(dtype=float)

    bids: dict[str, np.ndarray] = {
        "model": np.full(len(W), np.nan),
        "last_minus_2pct": np.where(np.isfinite(last), np.round(last * 0.98, 2), np.nan),
        "cost_plus_15pct": np.round(cost * 1.15, 2),
    }
    for i in range(len(W)):
        dist = PriceDistribution(levels, np.exp(logq[i]))
        rec = recommend_bid(dist, cost[i], qty[i], min_margin_pct=min_margin_pct, min_expected_profit=min_expected_profit)
        if not rec.no_bid:
            bids["model"][i] = rec.recommended_bid

    results = {}
    for name, b in bids.items():
        placed = np.isfinite(b)
        won = placed & (b < W)
        profit = np.where(won, (b - cost) * qty, 0.0)
        n_bids = int(placed.sum())
        results[name] = {
            "bids": n_bids,
            "wins": int(won.sum()),
            "win_rate": float(won.sum() / n_bids) if n_bids else 0.0,
            "total_profit": float(profit.sum()),
            "profit_per_bid": float(profit.sum() / n_bids) if n_bids else 0.0,
            "losing_wins": int((won & (b < cost)).sum()),  # won below cost
        }
    return {
        "n_lines": int(len(W)),
        "assumption": f"landed cost = winning price x U({COST_LOW}, {COST_HIGH}); bid wins iff bid < winning price",
        "min_margin_pct": min_margin_pct,
        "policies": results,
    }
