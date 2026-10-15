"""Portfolio view: the organization's open opportunities against its capital, capacity and concentration limits.

A per-line recommendation is right for the line and can still be wrong for the firm: it may have no hours left to
source it, no capital to carry it, or already rest too much on one supplier. This module answers those questions once
per organization from the open recommendations (no new maths per line), and returns the line ids each limit holds back
so the UI can say "beyond this week's capacity" or "on hold for capital" instead of silently dropping them.

Assumptions, stated: lines win independently of each other; capital is tied up from paying the supplier until DLA pays
(lead time + handling + payment terms); the hours per pursued line and the fulfilment-loss rate are org settings.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.timeutil import today_et
from app.models import OrgSettings, QuoteRequest, Recommendation, Solicitation, SolicitationLine, SupplierQuote
from app.services.biddable import not_awarded


@dataclass
class PortfolioSummary:
    # Lines priced against a quote and worth bidding
    quoted_lines: int = 0
    expected_awards: float = 0.0  # sum of P(win)
    expected_profit: float = 0.0
    capital_in_flight: float = 0.0  # sum of P(win) x landed x qty
    capital_if_all_won: float = 0.0
    capital_days: float = 0.0  # average days capital is tied up on a won line
    p_no_award: float = 1.0  # chance none of the bid lines is won
    # Limits
    working_capital_limit: float | None = None
    capital_hold: list[int] = field(default_factory=list)  # line ids beyond the capital limit (lowest rank first)
    max_open_awards: int | None = None
    awards_hold: list[int] = field(default_factory=list)
    # Sourcing capacity for unquoted lines worth pursuing
    pursue_candidates: int = 0  # lines with a positive triage score
    pursuit_hours_needed: float = 0.0
    weekly_sourcing_hours: float | None = None
    pursue_now: list[int] = field(default_factory=list)  # top lines by triage score that fit the hours
    deferred_capacity: list[int] = field(default_factory=list)
    pursuit_cost_committed: float = 0.0  # effort spent if nothing is won (downside)
    # Supplier concentration among quoted lines' expected capital
    supplier_shares: list[dict] = field(default_factory=list)
    max_supplier_share: float = 0.5
    concentrated_supplier: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


def portfolio_summary(db: Session, org_id: int, s: OrgSettings) -> PortfolioSummary:
    today = today_et()
    R, L, S = Recommendation, SolicitationLine, Solicitation
    ex = R.explanation
    rows = db.execute(
        select(L.id, L.quantity, L.delivery_days, R.p_win_at_recommended, R.expected_profit, R.landed_cost_per_unit,
               R.provisional, R.no_bid, R.recommended_bid, R.best_quote_id,
               ex["rank_score"].as_float().label("rank_score"), ex["triage_score"].as_float().label("triage_score"),
               ex[("triage", "pursuit_cost")].as_float().label("pursuit_cost"))
        .join(L, R.solicitation_line_id == L.id)
        .join(S, L.solicitation_id == S.id)
        .where(R.organization_id == org_id, or_(S.return_by_date.is_(None), S.return_by_date >= today), not_awarded(),
               L.fsc.in_(list(s.fsc_watchlist or [])) if s.fsc_watchlist else True)
    ).all()
    out = PortfolioSummary(working_capital_limit=s.working_capital_limit, max_open_awards=s.max_open_awards,
                           weekly_sourcing_hours=s.weekly_sourcing_hours, max_supplier_share=s.max_supplier_share)
    quoted = [r for r in rows if not r.no_bid and not r.provisional and r.recommended_bid is not None and r.p_win_at_recommended]
    pursue = [r for r in rows if not r.no_bid and r.provisional and (r.triage_score or 0) > 0]

    # Quoted lines: expected awards, profit, capital.
    quote_ids = [r.best_quote_id for r in quoted if r.best_quote_id]
    supplier_of: dict[int, tuple[str, int | None]] = {}
    if quote_ids:
        for qid, cage, lead in db.execute(
            select(SupplierQuote.id, QuoteRequest.cage_code, SupplierQuote.lead_time_days)
            .join(QuoteRequest, SupplierQuote.quote_request_id == QuoteRequest.id)
            .where(SupplierQuote.id.in_(quote_ids))
        ).all():
            supplier_of[qid] = ((cage or "").upper(), lead)
    p_none = 1.0
    days_weighted = 0.0
    by_supplier: dict[str, float] = defaultdict(float)
    for r in quoted:
        p = float(r.p_win_at_recommended)
        cap = float(r.landed_cost_per_unit or 0) * float(r.quantity or 0)
        out.quoted_lines += 1
        out.expected_awards += p
        out.expected_profit += float(r.expected_profit or 0)
        out.capital_in_flight += p * cap
        out.capital_if_all_won += cap
        p_none *= 1.0 - p
        cage, lead = supplier_of.get(r.best_quote_id or -1, ("", None))
        days = float(lead or r.delivery_days or 30) + float(s.handling_days or 0) + float(s.payment_terms_days or 0)
        days_weighted += p * cap * days
        if cage:
            by_supplier[cage] += p * cap
    out.p_no_award = p_none if quoted else 1.0
    out.capital_days = days_weighted / out.capital_in_flight if out.capital_in_flight > 0 else 0.0
    total_cap = sum(by_supplier.values())
    if total_cap > 0:
        shares = sorted(((c, v / total_cap, v) for c, v in by_supplier.items()), key=lambda t: -t[1])
        out.supplier_shares = [{"cage": c, "share": round(sh, 4), "capital": round(v, 2)} for c, sh, v in shares[:5]]
        if shares[0][1] > s.max_supplier_share and len(quoted) >= 3:
            out.concentrated_supplier = shares[0][0]
            out.notes.append(f"{100 * shares[0][1]:.0f}% of the expected capital rests on supplier {shares[0][0]} "
                             f"(limit {100 * s.max_supplier_share:.0f}%): a problem there hits most of the book")

    # Capital and award limits: hold back the lowest-ranked quoted lines until the expectation fits.
    if s.working_capital_limit is not None and out.capital_in_flight > s.working_capital_limit:
        running = out.capital_in_flight
        for r in sorted(quoted, key=lambda r: (r.rank_score or 0)):
            if running <= s.working_capital_limit:
                break
            running -= float(r.p_win_at_recommended) * float(r.landed_cost_per_unit or 0) * float(r.quantity or 0)
            out.capital_hold.append(r.id)
        out.notes.append(f"expected capital in flight ${out.capital_in_flight:,.0f} exceeds the ${s.working_capital_limit:,.0f} "
                         f"limit: {len(out.capital_hold)} lowest-ranked quoted lines on hold")
    if s.max_open_awards is not None and out.expected_awards > s.max_open_awards:
        running = out.expected_awards
        for r in sorted(quoted, key=lambda r: (r.rank_score or 0)):
            if running <= s.max_open_awards or r.id in out.capital_hold:
                if r.id in out.capital_hold:
                    running -= float(r.p_win_at_recommended)
                continue
            running -= float(r.p_win_at_recommended)
            out.awards_hold.append(r.id)
        out.notes.append(f"{out.expected_awards:.1f} expected awards exceed the {s.max_open_awards} the firm can fulfil: "
                         f"{len(out.awards_hold)} more lines on hold")

    # Sourcing capacity: pursue the best triage scores first until the week's hours are spent.
    pursue.sort(key=lambda r: -(r.triage_score or 0))
    out.pursue_candidates = len(pursue)
    hours_each = float(s.pursuit_hours_per_line or 0.33)
    out.pursuit_hours_needed = len(pursue) * hours_each
    if s.weekly_sourcing_hours is not None and hours_each > 0:
        fit = int(math.floor(s.weekly_sourcing_hours / hours_each))
        out.pursue_now = [r.id for r in pursue[:fit]]
        out.deferred_capacity = [r.id for r in pursue[fit:]]
        if out.deferred_capacity:
            out.notes.append(f"{len(pursue)} lines are worth pursuing but {s.weekly_sourcing_hours:g} h a week covers {fit}: "
                             f"the rest wait, best first")
    else:
        out.pursue_now = [r.id for r in pursue]
    out.pursuit_cost_committed = sum(float(r.pursuit_cost or 0) for r in pursue if r.id in set(out.pursue_now))
    return out
