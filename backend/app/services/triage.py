"""Triage: is this unquoted line worth the broker's sourcing effort?

    triage_score = E[profit | sourced at the estimated cost]
                   x P(a supplier price at or under the target cost)       (tenant cost model)
                   x P(at least one candidate supplier answers in time)    (tenant response history)
                   x P(the line is winnable by a broker at all)            (contestability, services/contestability.py)
                   - pursuit cost                                          (org setting: effort per line pursued)

It replaces "expected profit against an assumed cost" as the ordering of lines that still need a quote. Every
component is stored in the recommendation explanation so the broker can see why a line ranks where it does.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.timeutil import today_et
from app.models import OrgSettings, Solicitation, SolicitationLine
from app.services.contestability import Contestability
from app.services.cost_model import RefreshContext
from app.services.landed_cost import LandedCost
from dibbs_ml.cost_model import CostEstimate


@dataclass
class Triage:
    score: float
    expected_profit_if_sourced: float
    p_sourceable: float
    supplier_price_ceiling: float | None
    p_quote_in_time: float
    candidates: int
    candidates_with_email: int
    days_left: int | None
    p_winnable: float
    winnable_reason: str
    pursuit_cost: float

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


def supplier_price_ceiling(max_landed_cost: float | None, landed: LandedCost, s: OrgSettings) -> float | None:
    """The supplier unit price that lands at `max_landed_cost` once freight, packaging, inspection, first article,
    financing and overhead are added (the inverse of compute_landed_cost for the supplier-price term)."""
    if max_landed_cost is None:
        return None
    fixed = landed.freight_per_unit + landed.packaging_per_unit + landed.inspection_allowance + landed.first_article_allowance_per_unit
    multiplier = 1.0 + s.default_overhead_pct + s.cost_of_capital_pct * s.payment_terms_days / 365.0
    return max((max_landed_cost - fixed) / multiplier, 0.0)


def p_quote_in_time(ctx: RefreshContext, candidates, days_left: int | None) -> tuple[float, int, int]:
    """P(at least one candidate answers before the return-by date), from the tenant's own response history."""
    if days_left is not None and days_left < 0:
        return 0.0, len(candidates), 0
    p_none = 1.0
    with_email = 0
    for c in candidates:
        has_email = bool(c.contact and c.contact.email)
        with_email += int(has_email)
        p_reply = ctx.responses.rate(c.cage_code, has_email)
        if days_left is None:
            p_time = 1.0
        else:
            # Full credit when the deadline leaves twice the supplier's usual response time; none when it has passed.
            need = 2.0 * ctx.responses.median_days(c.cage_code)
            p_time = min(max(days_left / need, 0.0), 1.0) if need > 0 else 1.0
        p_none *= 1.0 - p_reply * p_time
    return 1.0 - p_none, len(candidates), with_email


def triage_line(
    db: Session,
    ctx: RefreshContext,
    line: SolicitationLine,
    sol: Solicitation,
    landed: LandedCost,
    cost_estimate: CostEstimate | None,
    expected_profit: float | None,
    max_landed_cost: float | None,
    contest: Contestability,
    candidates,
    deadline=None,
) -> Triage | None:
    """The triage of one provisional line. None when there is nothing to triage (no bid, no cost estimate)."""
    if expected_profit is None or cost_estimate is None:
        return None
    s = ctx.settings
    ceiling = supplier_price_ceiling(max_landed_cost, landed, s)
    p_source = cost_estimate.p_at_or_below(ceiling) if ceiling else 0.0
    today = today_et()
    # The sourcing deadline: the return-by date, or earlier when DLA's fast auto evaluation can award before it.
    deadline = deadline or sol.return_by_date
    days_left = (deadline - today).days if deadline else None
    p_quote, n_cand, n_email = p_quote_in_time(ctx, candidates, days_left)
    pursuit = float(s.pursuit_cost_per_line or 0.0)
    ep = max(float(expected_profit), 0.0)
    score = ep * p_source * p_quote * contest.p_winnable - pursuit
    return Triage(
        score=score,
        expected_profit_if_sourced=ep,
        p_sourceable=p_source,
        supplier_price_ceiling=ceiling,
        p_quote_in_time=p_quote,
        candidates=n_cand,
        candidates_with_email=n_email,
        days_left=days_left,
        p_winnable=contest.p_winnable,
        winnable_reason=contest.reason,
        pursuit_cost=pursuit,
    )
