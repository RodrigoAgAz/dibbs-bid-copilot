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
    rows = ctx.db.execute(
        select(Recommendation, SolicitationLine, Solicitation)
        .join(SolicitationLine, Recommendation.solicitation_line_id == SolicitationLine.id)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .where(
            or_(Solicitation.return_by_date.is_(None), Solicitation.return_by_date >= today),
            SolicitationLine.fsc.in_(fscs),
        )
    ).all()
    summaries = quote_summaries(ctx, [ln.id for _, ln, _ in rows])
    lines: list[DashboardLine] = []
    for rec, ln, sol in rows:
        ex = rec.explanation or {}
        lines.append(
            DashboardLine(
                line_id=ln.id,
                solicitation_number=sol.solicitation_number,
                line_no=ln.line_no,
                type_code=sol.type_code,
                return_by_date=sol.return_by_date,
                days_left=(sol.return_by_date - today).days if sol.return_by_date else None,
                nsn=ln.nsn,
                fsc=ln.fsc,
                nomenclature=ln.nomenclature,
                quantity=ln.quantity,
                unit_of_issue=ln.unit_of_issue,
                set_aside=sol.set_aside,
                predicted_median=(ex.get("distribution") or {}).get("median"),
                landed_cost=rec.landed_cost_per_unit,
                recommended_bid=rec.recommended_bid,
                conservative_bid=rec.conservative_bid,
                aggressive_bid=rec.aggressive_bid,
                p_win=rec.p_win_at_recommended,
                expected_profit=rec.expected_profit,
                no_bid=rec.no_bid,
                no_bid_reason=rec.no_bid_reason,
                actual_bid=rec.actual_bid,
                quote_status=summaries.get(ln.id, QuoteStatusSummary()),
                flags=Flags(
                    fast_award=sol.fast_award,
                    approved_source_only=ln.approved_source_only,
                    low_data=bool(ex.get("low_data")),
                    cold_start=bool(ex.get("cold_start")),
                    provisional=rec.provisional,
                    first_article=sol.first_article,
                    aidc=sol.aidc,
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
