"""Recommendation engine math: choose the bid that maximizes expected profit.

    P(win | b)   = 1 - F_W(b)                       (W = winning unit price, predicted distribution)
    E[profit](b) = P(win | b) * (b - landed) * quantity

The search is limited to bids with P(win) >= min_p_win. Without that floor, wide (low-data) distributions
push the argmax into the upper tail: a 5%-to-win bid at 70x the typical price can carry the largest
"expected profit" while being useless to a broker, and the tail is where the model is least reliable.

Used by the backend recommendation service and by the bidding backtest, so both use one engine.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from dibbs_ml.cdf import PriceDistribution

CONSERVATIVE_P_WIN = 0.7
AGGRESSIVE_P_WIN = 0.3


@dataclass
class BidRecommendation:
    landed_cost: float
    recommended_bid: float | None
    p_win: float | None
    expected_profit: float | None
    conservative_bid: float | None = None
    conservative_p_win: float | None = None
    aggressive_bid: float | None = None
    aggressive_p_win: float | None = None
    no_bid: bool = False
    no_bid_reasons: list[str] = field(default_factory=list)
    curve: list[dict] = field(default_factory=list)  # samples of (bid, p_win, expected_profit)
    grid_low: float | None = None
    grid_high: float | None = None

    @property
    def no_bid_reason(self) -> str | None:
        return "; ".join(self.no_bid_reasons) if self.no_bid_reasons else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["no_bid_reason"] = self.no_bid_reason
        return d


def price_decimals(x: float) -> int:
    """Bid precision: cents from $1 up; 4 decimals below $1 (DLA awards sub-dollar items at prices like $0.0482,
    where whole-cent steps would be up to 20% of the price)."""
    return 2 if x >= 1.0 else 4


def _ceil_to(x: float, d: int) -> float:
    f = 10 ** d
    return math.ceil(round(x * f, 6)) / f


def _floor_to(x: float, d: int) -> float:
    f = 10 ** d
    return math.floor(round(x * f, 6)) / f


def _ceil_cent(x: float) -> float:
    return _ceil_to(x, 2)


def recommend_bid(
    dist: PriceDistribution,
    landed_cost: float,
    quantity: float,
    min_margin_pct: float = 0.10,
    min_expected_profit: float = 0.0,
    max_line_value: float | None = None,
    approved_source_only: bool = False,
    has_conforming_quote: bool = True,
    return_by_passed: bool = False,
    min_p_win: float = 0.05,
    grid_points: int = 400,
    curve_points: int = 60,
) -> BidRecommendation:
    landed = float(landed_cost)
    qty = max(float(quantity), 0.0)
    floor_p = min(max(float(min_p_win), 0.05), 0.95)
    dp = price_decimals(min(landed * (1.0 + max(min_margin_pct, 0.0)), float(dist.ppf(1.0 - floor_p))))
    step = 10.0 ** -dp
    lo = _ceil_to(landed * (1.0 + max(min_margin_pct, 0.0)), dp)
    # Floor to the price step so the top of the grid never dips below the minimum chance to win.
    hi = _floor_to(float(dist.ppf(1.0 - floor_p)), dp)
    reasons: list[str] = []

    if lo > hi:
        # Even the minimum acceptable bid has less than floor_p chance of winning.
        grid = np.array([lo])
        reasons.append(
            f"cost too high: the minimum-margin bid has less than a {floor_p:.0%} chance of winning"
            if floor_p > 0.05 else
            "cost too high: minimum-margin bid exceeds the 95th percentile of the expected winning price"
        )
    else:
        grid = np.unique(np.round(np.linspace(lo, hi, grid_points), dp))
        grid = grid[grid >= lo]  # rounding must never go below landed * (1 + margin)

    p = np.asarray(dist.sf(grid), dtype=float).reshape(-1)
    ep = p * (grid - landed) * qty
    i = int(np.argmax(ep))
    # Refine the argmax to the price step between the neighbouring grid points.
    a, b = grid[max(i - 1, 0)], grid[min(i + 1, grid.size - 1)]
    f = 10 ** dp
    fine = np.round(np.arange(round(a * f), round(b * f) + 1) / f, dp)
    fine = fine[(fine >= lo) & (fine <= grid[-1])] if fine.size else fine
    if fine.size:
        pf = np.asarray(dist.sf(fine), dtype=float).reshape(-1)
        ef = pf * (fine - landed) * qty
        j = int(np.argmax(ef))
        best_bid, best_p, best_e = float(fine[j]), float(pf[j]), float(ef[j])
    else:
        best_bid, best_p, best_e = float(grid[i]), float(p[i]), float(ep[i])
    rec = BidRecommendation(
        landed_cost=round(landed, 4),
        recommended_bid=best_bid,
        p_win=best_p,
        expected_profit=best_e,
        grid_low=float(grid[0]),
        grid_high=float(grid[-1]),
    )

    # Conservative: the highest bid (highest margin) that still has P(win) >= 0.7, exact to the price step.
    cons = _floor_to(float(dist.ppf(1.0 - CONSERVATIVE_P_WIN)), dp)
    while cons >= lo and float(dist.sf(cons)) < CONSERVATIVE_P_WIN:
        cons = round(cons - step, dp)
    if cons >= lo:
        rec.conservative_bid, rec.conservative_p_win = cons, float(dist.sf(cons))
    # Aggressive: the allowed bid whose P(win) is closest to 0.3.
    agg = min(max(round(float(dist.ppf(1.0 - AGGRESSIVE_P_WIN)), dp), lo), max(lo, float(grid[-1])))
    rec.aggressive_bid, rec.aggressive_p_win = float(agg), float(dist.sf(agg))

    # The alternatives must bracket the recommendation: "conservative" is safer (a lower bid, higher P(win)) and
    # "aggressive" riskier. When the optimum already wins >= 70% of the time there is no safer option to offer.
    if rec.conservative_bid is not None and rec.recommended_bid is not None and rec.conservative_bid >= rec.recommended_bid:
        rec.conservative_bid = rec.conservative_p_win = None
    if rec.recommended_bid is not None and rec.aggressive_bid <= rec.recommended_bid:
        rec.aggressive_bid = rec.aggressive_p_win = None

    # Curve for the UI: from landed cost to the 97.5th percentile (shows the full trade-off).
    c_lo = max(landed, float(dist.ppf(0.01)) * 0.9)
    c_hi = max(float(dist.ppf(0.975)), lo * 1.05)
    xs = np.unique(np.round(np.linspace(c_lo, c_hi, curve_points), dp))
    ps = np.asarray(dist.sf(xs), dtype=float).reshape(-1)
    rec.curve = [
        {"bid": float(b), "p_win": round(float(pw), 4), "expected_profit": round(float(pw * (b - landed) * qty), 2)}
        for b, pw in zip(xs, ps, strict=True)
    ]

    if return_by_passed:
        reasons.insert(0, "return-by date has passed")
    if approved_source_only and not has_conforming_quote:
        reasons.append("approved-source-only item with no conforming quote for the approved part number")
    if max_line_value is not None and qty * dist.median > max_line_value:
        reasons.append(f"line value ${qty * dist.median:,.0f} exceeds the max line value ${max_line_value:,.0f}")
    if rec.expected_profit is not None and rec.expected_profit < min_expected_profit and not any(
        r.startswith("cost too high") for r in reasons
    ):
        reasons.append(f"expected profit ${rec.expected_profit:,.2f} is below the ${min_expected_profit:,.2f} floor")
    rec.no_bid_reasons = reasons
    rec.no_bid = bool(reasons)
    return rec
