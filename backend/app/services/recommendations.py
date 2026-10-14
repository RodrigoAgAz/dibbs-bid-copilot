"""Per-organization bid recommendations: landed cost + predicted W distribution -> recommend_bid."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.timeutil import today_et
from app.models import (
    OrgSettings,
    PricePrediction,
    QuoteRequest,
    Recommendation,
    Solicitation,
    SolicitationLine,
    SupplierQuote,
)
from app.services.landed_cost import LandedCost, compute_landed_cost
from app.services.pricing import distribution_of, fsc_median_index, latest_prediction, predict_line
from dibbs_ml.bidding import recommend_bid

log = logging.getLogger(__name__)

OPEN_SET_ASIDES = {"", "NONE", "N", "UNRESTRICTED", "FULL AND OPEN", "NA"}


def open_lines(db: Session, today: date | None = None, fscs: list[str] | None = None) -> list[SolicitationLine]:
    today = today or today_et()
    stmt = (
        select(SolicitationLine)
        .join(Solicitation)
        .options(joinedload(SolicitationLine.solicitation))
        .where(or_(Solicitation.return_by_date.is_(None), Solicitation.return_by_date >= today))
    )
    if fscs is not None:
        stmt = stmt.where(SolicitationLine.fsc.in_(fscs))
    return list(db.execute(stmt).scalars().unique())


def org_settings(db: Session, org_id: int) -> OrgSettings:
    s = db.get(OrgSettings, org_id)
    if s is None:
        s = OrgSettings(organization_id=org_id)
        db.add(s)
        db.flush()
    return s


def candidate_quotes(db: Session, org_id: int, line_id: int) -> list[SupplierQuote]:
    return list(
        db.execute(
            select(SupplierQuote)
            .join(QuoteRequest)
            .options(joinedload(SupplierQuote.quote_request))
            .where(
                QuoteRequest.organization_id == org_id,
                QuoteRequest.solicitation_line_id == line_id,
                SupplierQuote.organization_id == org_id,
                SupplierQuote.unit_price.is_not(None),
                SupplierQuote.declined.is_(False),
                or_(SupplierQuote.needs_review.is_(False), SupplierQuote.reviewed_at.is_not(None)),
            )
        ).scalars()
    )


def best_landed_cost(
    db: Session, org_id: int, s: OrgSettings, line: SolicitationLine, median: float
) -> tuple[LandedCost, bool, int]:
    """Best landed cost among usable quotes. Returns (landed, has_conforming_quote, n_quotes)."""
    quotes = candidate_quotes(db, org_id, line.id)
    usable = [q for q in quotes if q.conforms_to_part_number] if line.approved_source_only else quotes
    best: LandedCost | None = None
    for q in usable:
        lc = compute_landed_cost(line, line.solicitation, s, q, median)
        if best is None or lc.landed < best.landed:
            best = lc
    if best is None:
        best = compute_landed_cost(line, line.solicitation, s, None, median)
    return best, bool(usable), len(quotes)


def upsert_recommendation(
    db: Session,
    org_id: int,
    s: OrgSettings,
    line: SolicitationLine,
    pred: PricePrediction,
    today: date | None = None,
) -> Recommendation:
    today = today or today_et()
    sol = line.solicitation
    dist = distribution_of(pred)
    landed, has_conforming, n_quotes = best_landed_cost(db, org_id, s, line, dist.median)
    rec = recommend_bid(
        dist,
        landed.landed,
        line.quantity,
        min_margin_pct=s.min_margin_pct,
        min_expected_profit=s.min_expected_profit,
        max_line_value=s.max_line_value,
        min_p_win=s.min_p_win,
        approved_source_only=line.approved_source_only,
        has_conforming_quote=has_conforming,
        return_by_passed=bool(sol.return_by_date and sol.return_by_date < today),
    )
    reasons = list(rec.no_bid_reasons)
    sa = (sol.set_aside or "").strip().upper()
    eligible = {e.strip().upper() for e in (s.set_aside_eligibility or [])}
    if sa not in OPEN_SET_ASIDES and sa not in eligible:
        reasons.append(f"set-aside {sa} is not in your eligibility list")

    row = db.execute(
        select(Recommendation).where(
            Recommendation.organization_id == org_id, Recommendation.solicitation_line_id == line.id
        )
    ).scalar_one_or_none()
    if row is None:
        row = Recommendation(organization_id=org_id, solicitation_line_id=line.id)
        db.add(row)
    q = pred.quantiles
    row.price_prediction_id = pred.id
    row.best_quote_id = landed.quote_id
    row.landed_cost_per_unit = round(landed.landed, 4)
    row.landed_breakdown = landed.to_dict()
    row.provisional = landed.provisional
    row.recommended_bid = rec.recommended_bid
    row.p_win_at_recommended = rec.p_win
    row.expected_profit = rec.expected_profit
    row.conservative_bid = rec.conservative_bid
    row.aggressive_bid = rec.aggressive_bid
    row.no_bid = bool(reasons)
    row.no_bid_reason = "; ".join(reasons) if reasons else None
    row.explanation = {
        "inputs": {
            "quantity": line.quantity,
            "landed_cost": round(landed.landed, 4),
            "min_margin_pct": s.min_margin_pct,
            "min_expected_profit": s.min_expected_profit,
            "max_line_value": s.max_line_value,
            "approved_source_only": line.approved_source_only,
            "has_conforming_quote": has_conforming,
            "n_quotes": n_quotes,
        },
        "landed": landed.to_dict(),
        "distribution": {"levels": q["levels"], "prices": q["prices"], "median": q.get("median")},
        "curve": rec.curve,
        "conservative_p_win": rec.conservative_p_win,
        "aggressive_p_win": rec.aggressive_p_win,
        "grid": [rec.grid_low, rec.grid_high],
        "reasons": reasons,
        "low_data": q.get("low_data", False),
        "cold_start": q.get("cold_start", False),
        "provisional": landed.provisional,
        "model": q.get("model"),
        "contributions": q.get("contributions", []),
        "similar_awards": q.get("similar_awards", []),
        "reference_price": q.get("reference_price"),
        "reference_type": q.get("reference_type"),
    }
    return row


def ensure_prediction(db: Session, line: SolicitationLine, fsc_index=None) -> PricePrediction:
    pred = latest_prediction(db, line.id)
    if pred is None:
        pred = predict_line(db, line, fsc_index=fsc_index)
    return pred


def refresh_org_recommendations(
    db: Session, org_id: int, create_quote_drafts: bool = False, line_ids: list[int] | None = None,
    auto_quotes: bool = False,
) -> int:
    """(Re)compute recommendations for the org's open lines.

    create_quote_drafts: draft a quote request for every candidate supplier (demo seed).
    auto_quotes: only if the org turned on auto-send, draft and send to candidates that have an email address.
    Otherwise quote requests are drafted on demand from the RFQ page.
    """
    s = org_settings(db, org_id)
    lines = open_lines(db, fscs=list(s.fsc_watchlist or []))
    if line_ids is not None:
        wanted = set(line_ids)
        lines = [ln for ln in lines if ln.id in wanted]
    if not lines:
        return 0
    fsc_index = fsc_median_index(db, {ln.fsc for ln in lines}, today_et())
    n = 0
    for line in lines:
        pred = ensure_prediction(db, line, fsc_index)
        if create_quote_drafts or (auto_quotes and s.auto_send_rfq):
            from app.services.sourcing import create_draft_requests

            create_draft_requests(db, org_id, line, s, only_sendable=not create_quote_drafts)
        upsert_recommendation(db, org_id, s, line, pred)
        n += 1
    db.flush()
    return n


def refresh_line_for_org(db: Session, org_id: int, line: SolicitationLine) -> Recommendation:
    s = org_settings(db, org_id)
    pred = ensure_prediction(db, line)
    rec = upsert_recommendation(db, org_id, s, line, pred)
    db.flush()
    return rec


def refresh_stale_predictions(db: Session, limit: int | None = None) -> dict:
    """Re-price open lines whose NSN gained award history after they were priced, then refresh every org's
    recommendations for them.

    History arrives after a line is first priced all the time: the ingest fetches a bounded number of RFQ PDFs
    per run, the nightly backfill and award labeling add rows, and other solicitations for the same NSN bring
    their own procurement history. Without this, such lines keep a cold-start (FSC-wide) prediction.
    """
    from app.models import AwardHistory, Organization, PricePrediction

    lines = open_lines(db)
    if not lines:
        return {"stale": 0, "repredicted": 0}
    nsns = sorted({ln.nsn for ln in lines})
    counts: dict[str, int] = {}
    for i in range(0, len(nsns), 1000):
        counts.update(db.execute(
            select(AwardHistory.nsn, func.count())
            .where(AwardHistory.nsn.in_(nsns[i:i + 1000]), AwardHistory.unit_price > 0, AwardHistory.quantity > 0)
            .group_by(AwardHistory.nsn)
        ).all())
    ids = [ln.id for ln in lines]
    latest: dict[int, dict] = {}
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        newest = (
            select(PricePrediction.solicitation_line_id, func.max(PricePrediction.id).label("pid"))
            .where(PricePrediction.solicitation_line_id.in_(chunk))
            .group_by(PricePrediction.solicitation_line_id)
            .subquery()
        )
        latest.update(db.execute(
            select(PricePrediction.solicitation_line_id, PricePrediction.quantiles)
            .join(newest, PricePrediction.id == newest.c.pid)
        ).all())
    stale = []
    for ln in lines:
        q = latest.get(ln.id)
        used = None if q is None else q.get("history_rows", q.get("n_prior_awards", 0))
        if used is None or counts.get(ln.nsn, 0) > used:
            stale.append(ln)
    todo = stale[:limit] if limit else stale
    if todo:
        idx = fsc_median_index(db, {ln.fsc for ln in todo}, today_et())
        for ln in todo:
            predict_line(db, ln, fsc_index=idx)
        db.flush()
        todo_ids = [ln.id for ln in todo]
        for org_id in db.execute(select(Organization.id)).scalars():
            refresh_org_recommendations(db, org_id, line_ids=todo_ids)
        db.flush()
    return {"stale": len(stale), "repredicted": len(todo)}


def orgs_watching(db: Session, fsc: str) -> list[int]:
    return [s.organization_id for s in db.execute(select(OrgSettings)).scalars() if fsc in (s.fsc_watchlist or [])]
