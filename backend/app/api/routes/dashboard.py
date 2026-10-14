"""Today: the organization's open matched lines ranked by expected profit."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from app.api.deps import get_tenant
from app.core.tenancy import TenantContext
from app.core.timeutil import today_et
from app.models import (
    IngestionRun,
    QuoteRequest,
    QuoteRequestStatus,
    Recommendation,
    Solicitation,
    SolicitationLine,
    SupplierQuote,
)
from app.schemas.rfq import DashboardLine, DashboardOut, DashboardSummary, Flags, QuoteStatusSummary
from app.services.recommendations import org_settings

router = APIRouter(prefix="/api", tags=["dashboard"])


def quote_summaries(ctx: TenantContext, line_ids: list[int]) -> dict[int, QuoteStatusSummary]:
    out: dict[int, QuoteStatusSummary] = defaultdict(QuoteStatusSummary)
    if not line_ids:
        return out
    rows = ctx.db.execute(
        select(QuoteRequest.solicitation_line_id, QuoteRequest.status, func.count())
        .where(QuoteRequest.solicitation_line_id.in_(line_ids))
        .group_by(QuoteRequest.solicitation_line_id, QuoteRequest.status)
    ).all()
    for line_id, st, n in rows:
        s = out[line_id]
        setattr(s, st.value, getattr(s, st.value) + int(n))
        s.candidates += int(n)
    review = ctx.db.execute(
        select(QuoteRequest.solicitation_line_id, func.count())
        .join(SupplierQuote, SupplierQuote.quote_request_id == QuoteRequest.id)
        .where(QuoteRequest.solicitation_line_id.in_(line_ids), SupplierQuote.needs_review.is_(True),
               SupplierQuote.reviewed_at.is_(None))
        .group_by(QuoteRequest.solicitation_line_id)
    ).all()
    for line_id, n in review:
        out[line_id].needs_review = int(n)
    for s in out.values():
        if s.parsed:
            s.label = f"{s.parsed} quote{'s' if s.parsed > 1 else ''}"
        elif s.replied:
            s.label = "replied"
        elif s.sent:
            s.label = f"{s.sent} sent"
        elif s.draft:
            s.label = f"{s.draft} drafts"
        elif s.declined:
            s.label = "declined"
        elif s.no_response:
            s.label = "no response"
    return out


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    fsc: list[str] | None = Query(default=None),
    ctx: TenantContext = Depends(get_tenant),
) -> DashboardOut:
    today = today_et()
    s = org_settings(ctx.db, ctx.org_id)
    fscs = fsc or list(s.fsc_watchlist or [])
    # Columns only: loading whole Recommendation rows would pull every explanation JSON (curve, similar awards,
    # contributions), megabytes for the thousands of open lines a live org has. Three small values come from it.
    ex = Recommendation.explanation
    R, L, S = Recommendation, SolicitationLine, Solicitation
    rows = ctx.db.execute(
        select(
            L.id, S.solicitation_number, L.line_no, S.type_code, S.return_by_date, L.nsn, L.fsc, L.nomenclature,
            L.quantity, L.unit_of_issue, S.set_aside, R.landed_cost_per_unit, R.recommended_bid, R.conservative_bid,
            R.aggressive_bid, R.p_win_at_recommended, R.expected_profit, R.no_bid, R.no_bid_reason, R.actual_bid,
            R.provisional, S.fast_award, L.approved_source_only, S.first_article, S.aidc,
            ex[("distribution", "median")].as_float().label("median"),
            ex["low_data"].as_boolean().label("low_data"),
            ex["cold_start"].as_boolean().label("cold_start"),
        )
        .join(L, R.solicitation_line_id == L.id)
        .join(S, L.solicitation_id == S.id)
        .where(
            or_(S.return_by_date.is_(None), S.return_by_date >= today),
            L.fsc.in_(fscs),
        )
    ).all()
    summaries = quote_summaries(ctx, [r.id for r in rows])
    lines: list[DashboardLine] = []
    for r in rows:
        lines.append(
            DashboardLine(
                line_id=r.id,
                solicitation_number=r.solicitation_number,
                line_no=r.line_no,
                type_code=r.type_code,
                return_by_date=r.return_by_date,
                days_left=(r.return_by_date - today).days if r.return_by_date else None,
                nsn=r.nsn,
                fsc=r.fsc,
                nomenclature=r.nomenclature,
                quantity=r.quantity,
                unit_of_issue=r.unit_of_issue,
                set_aside=r.set_aside,
                predicted_median=r.median,
                landed_cost=r.landed_cost_per_unit,
                recommended_bid=r.recommended_bid,
                conservative_bid=r.conservative_bid,
                aggressive_bid=r.aggressive_bid,
                p_win=r.p_win_at_recommended,
                expected_profit=r.expected_profit,
                no_bid=r.no_bid,
                no_bid_reason=r.no_bid_reason,
                actual_bid=r.actual_bid,
                quote_status=summaries.get(r.id, QuoteStatusSummary()),
                flags=Flags(
                    fast_award=r.fast_award,
                    approved_source_only=r.approved_source_only,
                    low_data=bool(r.low_data),
                    cold_start=bool(r.cold_start),
                    provisional=r.provisional,
                    first_article=r.first_article,
                    aidc=r.aidc,
                ),
            )
        )
    lines.sort(key=lambda d: (d.no_bid, -(d.expected_profit or 0)))
    awaiting = sum(q.sent for q in summaries.values())
    last = ctx.db.execute(
        select(IngestionRun.finished_at).where(IngestionRun.status.in_(["success", "partial"]))
        .order_by(IngestionRun.finished_at.desc())
    ).scalars().first()
    return DashboardOut(
        summary=DashboardSummary(
            as_of=today,
            lines=len(lines),
            bid_lines=sum(1 for d in lines if not d.no_bid),
            no_bid_lines=sum(1 for d in lines if d.no_bid),
            total_expected_profit=round(sum((d.expected_profit or 0) for d in lines if not d.no_bid), 2),
            quotes_awaiting_reply=awaiting,
            needs_review=sum(q.needs_review for q in summaries.values()),
            last_ingest_at=last,
        ),
        lines=lines,
    )


__all__ = ["router", "QuoteRequestStatus"]
