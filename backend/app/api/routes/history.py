"""Past recommendations vs actual outcomes once awards post."""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import median

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import get_tenant
from app.core.tenancy import TenantContext
from app.core.timeutil import today_et
from app.models import AwardHistory, Organization, Recommendation, Solicitation, SolicitationLine
from app.schemas.rfq import HistoryOut, HistoryRow, HistoryStats
from app.services.data_quality import MAX_RATIO, RELIABLE_SOURCES
from app.services.ops import SYNTHETIC_PREFIX, hide_synthetic

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history", response_model=HistoryOut)
def history(ctx: TenantContext = Depends(get_tenant)) -> HistoryOut:
    today = today_et()
    org = ctx.db.get(Organization, ctx.org_id)
    rows = ctx.db.execute(
        select(Recommendation, SolicitationLine, Solicitation)
        .join(SolicitationLine, Recommendation.solicitation_line_id == SolicitationLine.id)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .where((Solicitation.return_by_date < today) | Recommendation.actual_bid.is_not(None))
        .where(*([~Solicitation.solicitation_number.startswith(SYNTHETIC_PREFIX)] if hide_synthetic() else []))
        .order_by(Solicitation.return_by_date.desc())
    ).all()
    sols = {sol.solicitation_number for _, _, sol in rows}
    awards: dict[tuple[str, str], AwardHistory] = {}
    if sols:
        for a in ctx.db.execute(select(AwardHistory).where(AwardHistory.solicitation_number.in_(sols))).scalars():
            awards[(a.solicitation_number, a.nsn)] = a
    # Verified price level per NSN (services/data_quality.py): a winning price derived from a contract total
    # (award listing / record page) is shown but only counts when it agrees with the NSN's verified prices.
    nsns = {a.nsn for a in awards.values() if a.source not in RELIABLE_SOURCES}
    verified_median: dict[str, float] = {}
    if nsns:
        by_nsn: dict[str, list[float]] = {}
        for nsn, price in ctx.db.execute(
            select(AwardHistory.nsn, AwardHistory.unit_price).where(
                AwardHistory.nsn.in_(nsns), AwardHistory.source.in_(RELIABLE_SOURCES), AwardHistory.unit_price > 0)
        ).all():
            by_nsn.setdefault(nsn, []).append(price)
        verified_median = {k: median(v) for k, v in by_nsn.items()}

    def price_verified(a: AwardHistory) -> bool:
        if a.source in RELIABLE_SOURCES:
            return True
        ref = verified_median.get(a.nsn)
        return bool(ref and a.unit_price and 1 / MAX_RATIO <= a.unit_price / ref <= MAX_RATIO)

    out: list[HistoryRow] = []
    won = lost = rec_decided = rec_would_win = 0
    margins: list[float] = []
    for rec, line, sol in rows:
        a = awards.get((sol.solicitation_number, line.nsn))
        if a is None:
            outcome = "pending" if (sol.return_by_date and (today - sol.return_by_date).days < 60) else "no_award_data"
        elif org and org.cage_code and a.awardee_cage == org.cage_code:
            outcome = "won"
        elif rec.actual_bid is not None:
            outcome = "lost"
        else:
            outcome = "awarded"  # awarded to someone else; you didn't record a bid, so it isn't a loss
        if rec.actual_bid is not None and outcome in ("won", "lost"):
            if outcome == "won":
                won += 1
                if rec.landed_cost_per_unit and rec.actual_bid:
                    margins.append((rec.actual_bid - rec.landed_cost_per_unit) / rec.actual_bid)
            else:
                lost += 1
        # Only recommendations last computed before the award posted count (a fast award can post before the
        # return-by date, and a recommendation recomputed after it could already know the winning price).
        before_award = a is not None and rec.updated_at is not None and rec.updated_at < datetime.combine(
            a.award_date, datetime.min.time(), tzinfo=UTC)
        verified = a is not None and price_verified(a)
        counted = bool(a is not None and before_award and verified and rec.recommended_bid is not None and not rec.no_bid)
        if counted:
            rec_decided += 1
            rec_would_win += int(rec.recommended_bid < a.unit_price)
        out.append(
            HistoryRow(
                line_id=line.id,
                solicitation_number=sol.solicitation_number,
                return_by_date=sol.return_by_date,
                nsn=line.nsn,
                nomenclature=line.nomenclature,
                quantity=line.quantity,
                recommended_bid=rec.recommended_bid,
                actual_bid=rec.actual_bid,
                landed_cost=rec.landed_cost_per_unit,
                no_bid=rec.no_bid,
                outcome=outcome,
                winning_price=a.unit_price if a else None,
                winning_price_verified=verified,
                awardee_cage=a.awardee_cage if a else None,
                award_date=a.award_date if a else None,
                counted=counted,
            )
        )
    decided = won + lost
    return HistoryOut(
        stats=HistoryStats(
            tracked=len(out),
            with_actual_bid=sum(1 for r in out if r.actual_bid is not None),
            decided=decided,
            won=won,
            lost=lost,
            win_rate=(won / decided) if decided else None,
            realized_margin_pct=(sum(margins) / len(margins)) if margins else None,
            recommendation_would_have_won=rec_would_win,
            recommendation_decided=rec_decided,
        ),
        rows=out,
    )
