"""Backtest of the bidding policy on test-period awards.

Assumption (documented in the report): a broker's landed cost for a line is the actual winning
price times U(0.6, 0.95). A bid b wins when b < W (lowest acceptable price wins; ties lose).
Profit of a won bid = (b - cost) * quantity. Lost bids earn 0. Every bid placed costs `pursuit_cost`
(the sourcing and quoting effort), won or lost.

Policies:
  * model:            the recommendation engine (argmax expected profit), skipping "no bid" lines
  * last_minus_2pct:  bid the last award price for the NSN minus 2% (skip if no prior award)
  * cost_plus_15pct:  bid landed cost + 15%

Every policy is subject to the same bid/no-bid rules as the model policy: a bid below landed cost plus the
minimum margin is not placed, and neither is one whose chance of winning (under the predicted distribution) is
below `min_p_win`. Without that, the naive policies "win" lines by bidding below cost, and the comparison is not
between pricing rules but between a policy that protects itself and two that don't.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dibbs_ml.bidding import recommend_bid
from dibbs_ml.cdf import QUANTILE_LEVELS, PriceDistribution

COST_LOW, COST_HIGH = 0.60, 0.95
DEFAULT_PURSUIT_COST = 15.0  # dollars of broker effort per bid placed (research, RFQs, quote entry)


def run_backtest(
    test_df: pd.DataFrame,
    logq: np.ndarray,
    levels=QUANTILE_LEVELS,
    min_margin_pct: float = 0.05,
    min_expected_profit: float = 0.0,
    min_p_win: float = 0.05,
    pursuit_cost: float = DEFAULT_PURSUIT_COST,
    seed: int = 42,
) -> dict:
    rng = np.random.default_rng(seed)
    W = test_df["unit_price"].to_numpy(dtype=float)
    qty = test_df["quantity"].to_numpy(dtype=float)
    cost = W * rng.uniform(COST_LOW, COST_HIGH, len(W))
    last = test_df["last_price_raw"].to_numpy(dtype=float)
    n = len(W)

    dists = [PriceDistribution(levels, np.exp(logq[i])) for i in range(n)]
    floor_bid = cost * (1.0 + min_margin_pct)

    bids: dict[str, np.ndarray] = {
        "model": np.full(n, np.nan),
        "last_minus_2pct": np.where(np.isfinite(last), np.round(last * 0.98, 2), np.nan),
        "cost_plus_15pct": np.round(cost * 1.15, 2),
    }
    for i in range(n):
        rec = recommend_bid(dists[i], cost[i], qty[i], min_margin_pct=min_margin_pct,
                            min_expected_profit=min_expected_profit, min_p_win=min_p_win)
        if not rec.no_bid:
            bids["model"][i] = rec.recommended_bid

    # The same gates for every policy: minimum margin over cost and the minimum chance of winning.
    gated: dict[str, int] = {}
    for name, b in bids.items():
        if name == "model":
            gated[name] = 0
            continue
        ok = np.isfinite(b)
        p = np.array([float(dists[i].sf(b[i])) if ok[i] else 0.0 for i in range(n)])
        allowed = ok & (b >= floor_bid - 1e-9) & (p >= min_p_win)
        gated[name] = int((ok & ~allowed).sum())
        bids[name] = np.where(allowed, b, np.nan)

    results = {}
    for name, b in bids.items():
        placed = np.isfinite(b)
        won = placed & (b < W)
        profit = np.where(won, (b - cost) * qty, 0.0)
        n_bids = int(placed.sum())
        total = float(profit.sum())
        results[name] = {
            "bids": n_bids,
            "wins": int(won.sum()),
            "win_rate": float(won.sum() / n_bids) if n_bids else 0.0,
            "total_profit": total,
            "profit_per_bid": float(total / n_bids) if n_bids else 0.0,
            "pursuit_cost": float(pursuit_cost * n_bids),
            "net_profit": float(total - pursuit_cost * n_bids),
            # Net of effort, over every line the policy looked at (so a policy that bids on everything is charged for it).
            "net_profit_per_line": float((total - pursuit_cost * n_bids) / n) if n else 0.0,
            "losing_wins": int((won & (b < cost)).sum()),  # won below cost
            "withheld_by_rules": gated[name],
        }
    return {
        "n_lines": int(n),
        "assumption": f"landed cost = winning price x U({COST_LOW}, {COST_HIGH}); bid wins iff bid < winning price",
        "min_margin_pct": min_margin_pct,
        "min_p_win": min_p_win,
        "pursuit_cost": pursuit_cost,
        "policies": results,
    }
