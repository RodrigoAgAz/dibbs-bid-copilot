"""Sensitivity of a line's recommendation: which plausible change would flip the decision.

A point-optimal bid is not a recommendation when a 10% move in the supplier's price turns it into a no-bid. For every
priced line three scenarios are re-run through the same engine and compared with the base case:

* supplier price +10% (the landed cost's supplier term)
* competitors 5% lower (the whole winning-price distribution shifted down 5%)
* competitors 5% higher

The block is stored in the explanation (`sensitivity`) and shown on the RFQ page; `fragile` is set when any scenario
flips bid <-> no-bid or moves the recommended bid by more than 15%.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from dibbs_ml.bidding import BidRecommendation, recommend_bid
from dibbs_ml.cdf import PriceDistribution

FRAGILE_BID_MOVE = 0.15


@dataclass
class Scenario:
    name: str
    recommended_bid: float | None
    p_win: float | None
    expected_profit: float | None
    no_bid: bool
    bid_change_pct: float | None
    flips: bool

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("recommended_bid", "p_win", "expected_profit", "bid_change_pct"):
            if d[k] is not None:
                d[k] = round(d[k], 4)
        return d


@dataclass
class Sensitivity:
    scenarios: list[Scenario] = field(default_factory=list)
    fragile: bool = False

    def to_dict(self) -> dict:
        return {"scenarios": [s.to_dict() for s in self.scenarios], "fragile": self.fragile}


def _compare(name: str, base: BidRecommendation, alt: BidRecommendation) -> Scenario:
    change = None
    if base.recommended_bid and alt.recommended_bid and not base.no_bid and not alt.no_bid:
        change = alt.recommended_bid / base.recommended_bid - 1.0
    flips = bool(base.no_bid) != bool(alt.no_bid) or (change is not None and abs(change) > FRAGILE_BID_MOVE)
    return Scenario(name, alt.recommended_bid if not alt.no_bid else None, alt.p_win if not alt.no_bid else None,
                    alt.expected_profit if not alt.no_bid else None, bool(alt.no_bid), change, flips)


def sensitivity(dist: PriceDistribution, landed: float, supplier_unit_price: float, quantity: float, base: BidRecommendation,
                **engine_kwargs) -> Sensitivity:
    """Re-run the engine for the three scenarios with the same org parameters (`engine_kwargs` as passed to recommend_bid)."""
    out = Sensitivity()
    kw = dict(engine_kwargs)
    # Supplier price +10%: only the supplier's term of the landed cost moves (freight, packaging etc. stay).
    alt = recommend_bid(dist, landed + 0.10 * supplier_unit_price, quantity, **kw)
    out.scenarios.append(_compare("supplier price +10%", base, alt))
    for label, factor in (("competitors 5% lower", 0.95), ("competitors 5% higher", 1.05)):
        shifted = PriceDistribution(dist.levels, np.asarray(dist.prices, dtype=float) * factor)
        alt = recommend_bid(shifted, landed, quantity, **kw)
        out.scenarios.append(_compare(label, base, alt))
    out.fragile = any(sc.flips for sc in out.scenarios)
    return out
