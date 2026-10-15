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
    RecommendationSnapshot,
    Solicitation,
    SolicitationLine,
    SupplierQuote,
)
from app.services.biddable import not_awarded
from app.services.competitors import fsc_competitors
from app.services.cost_model import RefreshContext, build_refresh_context, catalog_prices_for
from app.services.evaluation import evaluate_line
from app.services.landed_cost import LandedCost, compute_landed_cost, price_per_issue_unit
from app.services.pricing import distribution_of, fsc_median_index, latest_prediction, predict_line
from app.services.sensitivity import sensitivity
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
    # The NSN's own last awards must agree too (max/min of the last 5, inflation-adjusted): a narrow model range
    # over scattered history (e.g. a unit-of-issue change) is not "high" confidence.
    spread = q.get("recent_spread")
    if n >= 3 and width <= math.log(2.2) and (spread is None or spread <= 2.5):
        return "high"
    if n >= 2 and width <= math.log(4.5) and (spread is None or spread <= 6.0):
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
    db: Session, org_id: int, s: OrgSettings, line: SolicitationLine, median: float, cost_estimate=None,
) -> tuple[LandedCost, bool, int]:
    """Best landed cost among usable quotes. Returns (landed, has_conforming_quote, n_quotes).

    `cost_estimate`: the tenant cost model's estimate, used for the provisional cost when no quote is usable.

    Usable: conforming (approved-source-only lines), not expired, and able to deliver in time (lead time within
    the line's required delivery days, when both are known). A quote that can't meet the delivery date would
    otherwise set a cost the broker can't actually bid on.
    """
    quotes = candidate_quotes(db, org_id, line.id)
    usable = [q for q in quotes if q.conforms_to_part_number] if line.approved_source_only else quotes
    today = today_et()
    usable = [
        q for q in usable
        if not (q.quote_valid_until and q.quote_valid_until < today)
        and not (q.lead_time_days and line.delivery_days
                 and q.lead_time_days + int(getattr(s, 'handling_days', 0) or 0) > line.delivery_days)
        # A price quoted per a unit that cannot be converted to the line's unit of issue is not a cost.
        and price_per_issue_unit(q, line.unit_of_issue)[0] is not None
    ]
    best: LandedCost | None = None
    for q in usable:
        lc = compute_landed_cost(line, line.solicitation, s, q, median)
        if best is None or lc.landed < best.landed:
            best = lc
    if best is None:
        best = compute_landed_cost(line, line.solicitation, s, None, median, cost_estimate=cost_estimate)
    return best, bool(usable), len(quotes)


def upsert_recommendation(
    db: Session,
    org_id: int,
    s: OrgSettings,
    line: SolicitationLine,
    pred: PricePrediction,
    today: date | None = None,
    ctx: RefreshContext | None = None,
) -> Recommendation:
    from app.services.contestability import contestability
    from app.services.sourcing import candidate_suppliers
    from app.services.triage import triage_line

    today = today or today_et()
    sol = line.solicitation
    dist = distribution_of(pred)
    q = pred.quantiles
    if ctx is None:
        ctx = build_refresh_context(db, org_id, s)
    # Candidate suppliers serve three purposes here: the manufacturers behind the part (cost model tier), who could
    # be asked (P(quote in time)), and the drafts the sourcing step may create.
    candidates = candidate_suppliers(db, org_id, line)
    oem_cages = {c.cage_code.upper() for c in candidates if c.reason == "approved_source"}
    approved_parts = [(c.cage_code, pn) for c in candidates if c.reason == "approved_source" for pn in c.part_numbers]
    catalog = catalog_prices_for(ctx.catalog, approved_parts) if ctx.catalog else []
    cost_est = ctx.cost_model.estimate(line.nsn, line.fsc, oem_cages, None if q.get("no_reference") else dist.median,
                                       line.quantity, catalog_prices=catalog)
    landed, has_conforming, n_quotes = best_landed_cost(db, org_id, s, line, dist.median, cost_estimate=cost_est)
    target_p = s.min_p_win if not landed.provisional else max(s.min_p_win, PROVISIONAL_MIN_P_WIN)
    best_quote = db.get(SupplierQuote, landed.quote_id) if landed.quote_id else None
    # How DLA evaluates this buy (services/evaluation.py): automated buys are filtered by price-reasonableness logic,
    # so the bid search is capped at the band DLA has actually awarded within; fast awards move the deadline up.
    fsc_comp = fsc_competitors(db, line.fsc)
    ev = evaluate_line(line, sol, s, q.get("reference_price"), fsc_comp, best_quote, bool(oem_cages), today)
    engine_kwargs = dict(
        min_margin_pct=s.min_margin_pct,
        min_expected_profit=s.min_expected_profit,
        max_line_value=s.max_line_value,
        min_p_win=target_p,
        approved_source_only=line.approved_source_only,
        has_conforming_quote=has_conforming,
        return_by_passed=bool(sol.return_by_date and sol.return_by_date < today),
        max_bid=ev.cap_bid,
        max_bid_reason=(f"cost too high: the minimum-margin bid is above DLA's automated price band "
                        f"(${ev.cap_bid:,.2f}, {ev.cap_ratio:.2f}x the previous award)") if ev.cap_bid else None,
        # Expected fulfilment losses (rejections, expediting, replacements) come off every won line.
        fulfilment_loss_pct=float(getattr(s, "fulfilment_loss_pct", 0.0) or 0.0),
    )
    rec = recommend_bid(dist, landed.landed, line.quantity, **engine_kwargs)
    ev.cap_binding = bool(rec.capped)
    reasons = list(rec.no_bid_reasons)
    sa = (sol.set_aside or "").strip().upper()
    eligible = {e.strip().upper() for e in (s.set_aside_eligibility or [])}
    if sa not in OPEN_SET_ASIDES and sa not in eligible:
        reasons.append(f"set-aside {sa} is not in your eligibility list")

    confidence = confidence_tier(q)
    if (landed.provisional and q.get("cold_start")) or q.get("no_reference"):
        # Quote first: the cost is a placeholder derived from the same guess as the price, so cost-based no-bid
        # reasons ("cost too high", "expected profit below ...") would be invented too. Hard rules still apply.
        reasons = [r for r in reasons if not (r.startswith("cost too high") or "expected profit" in r)]
    # Quote first: with no award history for the NSN and no supplier quote, both the price range (FSC-wide) and
    # the provisional cost (a fraction of that range's median) come from the same guess, so a "best bid" and its
    # expected profit would be invented (live: bids at ~10x the median). Show the range, ask for a quote.
    needs_quote = bool(landed.provisional and q.get("cold_start"))
    # No reference at all (no history for the NSN, its family or its FSC): there is no price to show or optimize,
    # with or without a quote; with a quote the broker gets a cost-plus figure instead of a bid.
    no_reference = bool(q.get("no_reference"))
    # Quote-first ranges spanning more than 20x (common: median 330x on live data) carry no information.
    uninformative = needs_quote and float(dist.ppf(0.9)) > 20 * float(dist.ppf(0.1))

    row = db.execute(
        select(Recommendation).where(
            Recommendation.organization_id == org_id, Recommendation.solicitation_line_id == line.id
        )
    ).scalar_one_or_none()
    if row is None:
        row = Recommendation(organization_id=org_id, solicitation_line_id=line.id)
        db.add(row)
    before = _decision_state(row)
    row.price_prediction_id = pred.id
    row.best_quote_id = landed.quote_id
    row.landed_cost_per_unit = round(landed.landed, 4)
    row.landed_breakdown = landed.to_dict()
    row.provisional = landed.provisional
    priced = not needs_quote and not no_reference
    row.recommended_bid = rec.recommended_bid if priced else None
    row.p_win_at_recommended = rec.p_win if priced else None
    row.expected_profit = rec.expected_profit if priced else None
    row.conservative_bid = rec.conservative_bid if priced else None
    row.aggressive_bid = rec.aggressive_bid if priced else None
    row.no_bid = bool(reasons)
    row.no_bid_reason = "; ".join(reasons) if reasons else None
    # Expected profit against an assumed cost is not a ranking signal: only lines priced against a real quote get a
    # rank score. Provisional lines are ordered by order size x confidence, labelled as such, until a quote is in.
    weight = CONFIDENCE_WEIGHT[confidence]
    rank_score = round((rec.expected_profit or 0.0) * weight, 2) if priced and not landed.provisional else None
    provisional_rank = round(float(line.quantity) * dist.median * weight, 2) if priced and landed.provisional else None
    max_landed_cost = (round(rec.recommended_bid / (1.0 + s.min_margin_pct), 4)
                       if landed.provisional and priced and rec.recommended_bid else None)
    # Contestability for every priced line; the triage score only for lines still waiting on a quote.
    contest = contestability(db, line, org_id) if priced else None
    # Which plausible change would flip this decision (services/sensitivity.py), for every priced line.
    sens = sensitivity(dist, landed.landed, landed.effective_unit_price, line.quantity, rec, **engine_kwargs) if priced else None
    # Competitor view of the recommended bid: P(nobody likely to bid comes in under it). Shown beside the calibrated
    # model's P(win), never used in its place (only winning prices are observed).
    comp_view = None
    if contest is not None and contest.competitors is not None and priced and rec.recommended_bid:
        comp_view = contest.competitors.p_win(rec.recommended_bid, q.get("reference_price"))
    triage = None
    if priced and landed.provisional and not reasons:
        triage = triage_line(db, ctx, line, sol, landed, cost_est, rec.expected_profit, max_landed_cost, contest, candidates,
                             deadline=ev.effective_deadline)
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
        "distribution": {"levels": q["levels"], "prices": q["prices"],
                         "median": None if (no_reference or uninformative) else q.get("median")},
        "curve": [] if no_reference else rec.curve,
        "conservative_p_win": rec.conservative_p_win,
        "aggressive_p_win": rec.aggressive_p_win,
        "grid": [rec.grid_low, rec.grid_high],
        "reasons": reasons,
        "low_data": q.get("low_data", False),
        "cold_start": q.get("cold_start", False),
        "provisional": landed.provisional,
        "needs_quote": needs_quote,
        "no_reference": no_reference,
        "cost_plus_bid": (round(landed.landed * (1.0 + s.min_margin_pct), 4)
                          if no_reference and not landed.provisional else None),
        "price_range": None if (no_reference or uninformative)
        else [round(float(dist.ppf(0.1)), 4), round(float(dist.ppf(0.9)), 4)],
        "uninformative_range": uninformative,
        "confidence": confidence,
        "n_prior_awards": q.get("n_prior_awards"),
        "target_p_win": target_p,
        # Provisional lines: the landed cost a supplier quote must come in under for the minimum margin at this bid.
        "max_landed_cost": max_landed_cost,
        # "quote": expected profit is computed against a supplier's price. "assumed_cost": against the tenant cost
        # model's estimate (or the flat fraction), so it says "if you can source at or under max_landed_cost".
        "expected_profit_basis": None if not priced else ("assumed_cost" if landed.provisional else "quote"),
        "rank_score": rank_score,
        "provisional_rank": provisional_rank,
        # Is this unquoted line worth sourcing effort? (services/triage.py; None once a quote prices the line.)
        "triage": triage.to_dict() if triage else None,
        "triage_score": round(triage.score, 2) if triage else None,
        "cost_estimate": cost_est.to_dict() if (cost_est is not None and landed.provisional) else None,
        "contestability": contest.to_dict() if contest else None,
        "competitors": contest.competitors.to_dict() if (contest and contest.competitors) else None,
        "competitor_p_win": None if comp_view is None else round(comp_view, 4),
        # How DLA evaluates this buy and what it did to the bid (services/evaluation.py).
        "evaluation": ev.to_dict(),
        "sensitivity": sens.to_dict() if sens else None,
        "fulfilment_loss_pct": rec.fulfilment_loss_pct,
        "quote_evidence": (best_quote.evidence or {}) if best_quote is not None else None,
        "model": q.get("model"),
        "contributions": q.get("contributions", []),
        "similar_awards": q.get("similar_awards", []),
        "reference_price": q.get("reference_price"),
        "reference_type": q.get("reference_type"),
    }
    if _decision_state(row) != before:
        db.flush()  # the snapshot needs the recommendation id
        db.add(RecommendationSnapshot(
            organization_id=org_id, solicitation_line_id=line.id, recommendation_id=row.id, kind="recommendation",
            engine_version=ENGINE_VERSION, payload=snapshot_payload(row, pred, usable_quote_ids=[landed.quote_id] if landed.quote_id else []),
        ))
    return row


def _decision_state(row: Recommendation) -> tuple:
    """The values whose change is a new decision and therefore a new snapshot."""
    return (
        row.recommended_bid, row.landed_cost_per_unit, row.p_win_at_recommended, row.no_bid, row.no_bid_reason,
        row.provisional, row.best_quote_id, row.conservative_bid, row.aggressive_bid,
    )


def snapshot_payload(row: Recommendation, pred: PricePrediction | None, usable_quote_ids: list[int] | None = None,
                     **extra) -> dict:
    """Everything the broker was shown for the decision, small enough to keep forever (no curve)."""
    ex = row.explanation or {}
    q = (pred.quantiles if pred is not None else None) or {}
    return {
        "prediction_id": pred.id if pred is not None else row.price_prediction_id,
        "model": q.get("model"),
        "quantiles": {"levels": q.get("levels"), "prices": q.get("prices")},
        "median": q.get("median"),
        "landed": row.landed_breakdown,
        "landed_cost_per_unit": row.landed_cost_per_unit,
        "provisional": row.provisional,
        "quotes_used": usable_quote_ids or ([row.best_quote_id] if row.best_quote_id else []),
        "recommended_bid": row.recommended_bid,
        "conservative_bid": row.conservative_bid,
        "aggressive_bid": row.aggressive_bid,
        "p_win": row.p_win_at_recommended,
        "expected_profit": row.expected_profit,
        "expected_profit_basis": ex.get("expected_profit_basis"),
        "no_bid": row.no_bid,
        "reasons": ex.get("reasons", []),
        "confidence": ex.get("confidence"),
        "target_p_win": ex.get("target_p_win"),
        "max_landed_cost": ex.get("max_landed_cost"),
        "actual_bid": row.actual_bid,
        "actual_cost_per_unit": row.actual_cost_per_unit,
        **extra,
    }


def record_snapshot(db: Session, row: Recommendation, kind: str, **extra) -> RecommendationSnapshot:
    """Append a snapshot for a broker action (actual bid, actual cost)."""
    pred = db.get(PricePrediction, row.price_prediction_id) if row.price_prediction_id else None
    snap = RecommendationSnapshot(
        organization_id=row.organization_id, solicitation_line_id=row.solicitation_line_id, recommendation_id=row.id,
        kind=kind, engine_version=ENGINE_VERSION, payload=snapshot_payload(row, pred, **extra),
    )
    db.add(snap)
    return snap


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
    ctx = build_refresh_context(db, org_id, s)
    n = 0
    for line in lines:
        pred = ensure_prediction(db, line, fsc_index)
        if create_quote_drafts or (auto_quotes and s.auto_send_rfq):
            from app.services.sourcing import create_draft_requests

            create_draft_requests(db, org_id, line, s, only_sendable=not create_quote_drafts)
        upsert_recommendation(db, org_id, s, line, pred, ctx=ctx)
        n += 1
    db.flush()
    return n


def refresh_line_for_org(db: Session, org_id: int, line: SolicitationLine) -> Recommendation:
    s = org_settings(db, org_id)
    pred = ensure_prediction(db, line)
    rec = upsert_recommendation(db, org_id, s, line, pred, ctx=build_refresh_context(db, org_id, s))
    db.flush()
    return rec


# Bump when the recommendation rules change: the next boot re-computes every open line once
# (app.cli bootstrap -> refresh_all_recommendations_job). 2 = minimum win probability, 3 = quote first,
# 4 = provisional bids target the likely winning price + confidence tiers, 5 = alternatives bracket the bid,
# 6 = no cost-based no-bid reasons on quote-first lines, 7 = no price shown without any reference,
# 8 = sub-dollar precision, recent-spread confidence, late/expired quotes ignored, uninformative ranges hidden,
# 9 = no rank score from an assumed cost (provisional lines ordered by size x confidence), quote evidence states,
#     pricing-unit normalization, decision snapshots,
# 10 = tenant cost model for the provisional cost, triage score (sourceable x quote-in-time x winnable - pursuit
#      cost) for unquoted lines, contestability,
# 11 = competitor model: reseller-share contestability, incumbents and supply-class players, competitor view of P(win),
# 12 = DLA evaluation rules: automated price band caps the bid, handling days in delivery feasibility, fast-award
#      deadlines, exceptions and evaluation factors shown,
# 13 = fulfilment losses in expected profit, sensitivity scenarios per line, portfolio limits (capital, awards, hours,
#      supplier concentration) in services/portfolio.py.
ENGINE_VERSION = "13"


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
        if used is None or counts.get(ln.nsn, 0) > used or q.get("no_reference"):
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
