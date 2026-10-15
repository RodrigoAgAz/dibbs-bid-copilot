"""Per-organization bid recommendations: landed cost + predicted W distribution -> recommend_bid."""

from __future__ import annotations

import logging
import math
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
from app.services.biddable import not_awarded
from app.services.landed_cost import LandedCost, compute_landed_cost
from app.services.pricing import distribution_of, fsc_median_index, latest_prediction, predict_line
from dibbs_ml.bidding import recommend_bid

log = logging.getLogger(__name__)

OPEN_SET_ASIDES = {"", "NONE", "N", "UNRESTRICTED", "FULL AND OPEN", "NA"}

# Without a supplier quote the landed cost is only an assumption (a fraction of the predicted price), so the
# bid is not optimized against it: it targets the price that is likely to win (P(win) >= 50%), and the broker is
# told the landed cost a supplier must beat. With a real quote the org's own floor (min_p_win) applies.
PROVISIONAL_MIN_P_WIN = 0.5
# Ranking weight per confidence tier ("best bets" = expected profit x weight).
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3, "none": 0.0}


def confidence_tier(q: dict) -> str:
    """How much the price prediction can be trusted, from award count and the width of the 80% range.

    high: >= 3 prior awards and q90/q10 <= 2.2;  medium: >= 2 awards and q90/q10 <= 4.5;  low: anything else
    with history;  none: no award history for the NSN (cold start).
    """
    if q.get("cold_start"):
        return "none"
    levels, prices = list(q.get("levels") or []), list(q.get("prices") or [])
    try:
        width = math.log(prices[levels.index(0.9)] / prices[levels.index(0.1)])
    except (ValueError, ZeroDivisionError, IndexError):
        return "low"
    n = int(q.get("n_prior_awards") or 0)
    if n >= 3 and width <= math.log(2.2):
        return "high"
    if n >= 2 and width <= math.log(4.5):
        return "medium"
    return "low"


def open_lines(db: Session, today: date | None = None, fscs: list[str] | None = None) -> list[SolicitationLine]:
    today = today or today_et()
    stmt = (
        select(SolicitationLine)
        .join(Solicitation)
        .options(joinedload(SolicitationLine.solicitation))
        .where(or_(Solicitation.return_by_date.is_(None), Solicitation.return_by_date >= today), not_awarded())
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
    target_p = s.min_p_win if not landed.provisional else max(s.min_p_win, PROVISIONAL_MIN_P_WIN)
    rec = recommend_bid(
        dist,
        landed.landed,
        line.quantity,
        min_margin_pct=s.min_margin_pct,
        min_expected_profit=s.min_expected_profit,
        max_line_value=s.max_line_value,
        min_p_win=target_p,
        approved_source_only=line.approved_source_only,
        has_conforming_quote=has_conforming,
        return_by_passed=bool(sol.return_by_date and sol.return_by_date < today),
    )
    reasons = list(rec.no_bid_reasons)
    sa = (sol.set_aside or "").strip().upper()
    eligible = {e.strip().upper() for e in (s.set_aside_eligibility or [])}
    if sa not in OPEN_SET_ASIDES and sa not in eligible:
        reasons.append(f"set-aside {sa} is not in your eligibility list")

    q = pred.quantiles
    confidence = confidence_tier(q)
    # Quote first: with no award history for the NSN and no supplier quote, both the price range (FSC-wide) and
    # the provisional cost (a fraction of that range's median) come from the same guess, so a "best bid" and its
    # expected profit would be invented (live: bids at ~10x the median). Show the range, ask for a quote.
    needs_quote = bool(landed.provisional and q.get("cold_start"))

    row = db.execute(
        select(Recommendation).where(
            Recommendation.organization_id == org_id, Recommendation.solicitation_line_id == line.id
        )
    ).scalar_one_or_none()
    if row is None:
        row = Recommendation(organization_id=org_id, solicitation_line_id=line.id)
        db.add(row)
    row.price_prediction_id = pred.id
    row.best_quote_id = landed.quote_id
    row.landed_cost_per_unit = round(landed.landed, 4)
    row.landed_breakdown = landed.to_dict()
    row.provisional = landed.provisional
    priced = not needs_quote
    row.recommended_bid = rec.recommended_bid if priced else None
    row.p_win_at_recommended = rec.p_win if priced else None
    row.expected_profit = rec.expected_profit if priced else None
    row.conservative_bid = rec.conservative_bid if priced else None
    row.aggressive_bid = rec.aggressive_bid if priced else None
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
        "needs_quote": needs_quote,
        "price_range": [round(float(dist.ppf(0.1)), 4), round(float(dist.ppf(0.9)), 4)],
        "confidence": confidence,
        "n_prior_awards": q.get("n_prior_awards"),
        "target_p_win": target_p,
        # Provisional lines: the landed cost a supplier quote must come in under for the minimum margin at this bid.
        "max_landed_cost": (round(rec.recommended_bid / (1.0 + s.min_margin_pct), 4)
                            if landed.provisional and priced and rec.recommended_bid else None),
        "rank_score": round((rec.expected_profit or 0.0) * CONFIDENCE_WEIGHT[confidence], 2) if priced else None,
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


# Bump when the recommendation rules change: the next boot re-computes every open line once
# (app.cli bootstrap -> refresh_all_recommendations_job). 2 = minimum win probability, 3 = quote first,
# 4 = provisional bids target the likely winning price + confidence tiers, 5 = alternatives bracket the bid.
ENGINE_VERSION = "5"


def refresh_all_recommendations(db: Session) -> dict:
    """Recompute every org's recommendations for its open lines (after a rules change)."""
    from app.models import Organization

    out = {}
    for org_id in list(db.execute(select(Organization.id)).scalars()):
        out[org_id] = refresh_org_recommendations(db, org_id)
        db.commit()
    return out


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
