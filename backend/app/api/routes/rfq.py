"""RFQ detail: solicitation facts, per-line prediction, recommendation, history, suppliers, quotes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_tenant
from app.core.tenancy import TenantContext
from app.core.timeutil import today_et
from app.models import (
    ApprovedSource,
    AwardHistory,
    CallLog,
    Company,
    EmailMessage,
    Organization,
    QuoteRequest,
    Recommendation,
    Solicitation,
    SolicitationLine,
    SupplierContact,
    SupplierQuote,
)
from app.schemas.rfq import (
    ActualBidIn,
    ApprovedSourceOut,
    AwardOut,
    CallLogOut,
    CandidateOut,
    EmailOut,
    LineDetail,
    PredictionOut,
    QuoteRequestOut,
    RecommendationOut,
    SolicitationDetail,
    SupplierQuoteOut,
)
from app.services.email.providers import reply_address
from app.services.pricing import latest_prediction
from app.services.recommendations import refresh_line_for_org
from app.services.sourcing import candidate_suppliers
from app.services.voice.guardrails import check_call_allowed

router = APIRouter(prefix="/api/rfq", tags=["rfq"])

TYPE_LABELS = {"T": "Automated (T)", "U": "Automated IDC / AIDC (U)", "Q": "Manual (Q)"}


def dibbs_url(sol_number: str) -> str:
    return f"https://www.dibbs.bsm.dla.mil/rfq/rfqrec.aspx?sn={sol_number.replace('-', '')}"


def _named(ctx: TenantContext, items: list[dict]) -> list[dict]:
    """Add item names to similar-item evidence (public catalog data)."""
    if not items:
        return []
    names = dict(ctx.db.execute(
        select(SolicitationLine.nsn, SolicitationLine.nomenclature)
        .where(SolicitationLine.nsn.in_([i["nsn"] for i in items]), SolicitationLine.nomenclature.is_not(None))
    ).all())
    return [{**i, "nomenclature": names.get(i["nsn"])} for i in items]


def quote_request_out(ctx: TenantContext, qr: QuoteRequest) -> QuoteRequestOut:
    emails = ctx.db.execute(
        select(EmailMessage).where(EmailMessage.quote_request_id == qr.id).order_by(EmailMessage.created_at)
    ).scalars().all()
    calls = ctx.db.execute(
        select(CallLog).where(CallLog.quote_request_id == qr.id).order_by(CallLog.started_at)
    ).scalars().all()
    quotes = ctx.db.execute(
        select(SupplierQuote).where(SupplierQuote.quote_request_id == qr.id).order_by(SupplierQuote.parsed_at)
    ).scalars().all()
    return QuoteRequestOut(
        id=qr.id,
        cage_code=qr.cage_code,
        channel=qr.channel.value,
        status=qr.status.value,
        candidate_reason=qr.candidate_reason,
        part_number=qr.part_number,
        email_to=qr.email_to,
        email_subject=qr.email_subject,
        email_body=qr.email_body,
        reply_address=reply_address(qr.reply_token),
        followup_count=qr.followup_count,
        error=qr.error,
        sent_at=qr.sent_at,
        replied_at=qr.replied_at,
        quotes=[SupplierQuoteOut.model_validate(q) for q in quotes],
        emails=[EmailOut.model_validate(e) for e in emails],
        calls=[CallLogOut.model_validate(c) for c in calls],
    )


def _line_detail(ctx: TenantContext, line: SolicitationLine, org: Organization) -> LineDetail:
    db = ctx.db
    rec = db.execute(
        select(Recommendation).where(Recommendation.solicitation_line_id == line.id)
    ).scalar_one_or_none()
    if rec is None:
        rec = refresh_line_for_org(db, ctx.org_id, line)
        db.commit()
    pred = latest_prediction(db, line.id)
    awards = db.execute(
        select(AwardHistory).where(AwardHistory.nsn == line.nsn).order_by(AwardHistory.award_date)
    ).scalars().all()
    cages = {a.awardee_cage for a in awards if a.awardee_cage}
    names = {c.cage_code: c.name for c in db.execute(select(Company).where(Company.cage_code.in_(cages))).scalars()} if cages else {}
    sources = db.execute(select(ApprovedSource).where(ApprovedSource.nsn == line.nsn)).scalars().all()
    qrs = db.execute(
        select(QuoteRequest).where(QuoteRequest.solicitation_line_id == line.id).order_by(QuoteRequest.id)
    ).scalars().all()
    by_cage: dict[str, list[QuoteRequest]] = {}
    for qr in qrs:
        by_cage.setdefault(qr.cage_code, []).append(qr)

    candidates: list[CandidateOut] = []
    cands = candidate_suppliers(db, ctx.org_id, line)
    extra = [c for c in by_cage if c not in {x.cage_code for x in cands}]
    rows = [(c.cage_code, c.reason, c.part_numbers, c.awards) for c in cands] + [(c, "manual", [], 0) for c in extra]
    for cage, reason, pns, n_awards in rows:
        contact = db.execute(select(SupplierContact).where(SupplierContact.cage_code == cage)).scalars().first()
        comp = db.get(Company, cage)
        callable_, block = False, []
        if contact is not None:
            g = check_call_allowed(db, ctx.org_id, contact)
            callable_, block = g.allowed, g.reasons
        else:
            block = ["no saved contact for this supplier"]
        candidates.append(
            CandidateOut(
                cage_code=cage,
                company_name=(contact.company_name if contact and contact.company_name else None) or (comp.name if comp else None),
                reason=reason,
                part_numbers=pns,
                historical_awards=n_awards,
                supplier_contact_id=contact.id if contact else None,
                contact_name=contact.name if contact else None,
                email=contact.email if contact else None,
                phone=contact.phone if contact else None,
                phone_type=contact.phone_type.value if contact else None,
                call_consent=bool(contact and contact.call_consent),
                callable=callable_,
                call_block_reasons=block,
                quote_requests=[quote_request_out(ctx, q) for q in by_cage.get(cage, [])],
            )
        )

    outcome = None
    sol_awards = [a for a in awards if a.solicitation_number == line.solicitation.solicitation_number]
    if sol_awards:
        a = sol_awards[-1]
        outcome = {
            "awardee_cage": a.awardee_cage,
            "winning_price": a.unit_price,
            "award_date": a.award_date.isoformat(),
            "won": bool(org.cage_code and a.awardee_cage == org.cage_code),
        }
    q = pred.quantiles if pred else None
    return LineDetail(
        id=line.id,
        line_no=line.line_no,
        nsn=line.nsn,
        fsc=line.fsc,
        niin=line.niin,
        nomenclature=line.nomenclature,
        quantity=line.quantity,
        unit_of_issue=line.unit_of_issue,
        dod_standard_price=line.dod_standard_price,
        amsc=line.amsc,
        approved_source_only=line.approved_source_only,
        delivery_days=line.delivery_days,
        purchase_request=line.purchase_request,
        prediction=PredictionOut(
            levels=q["levels"],
            prices=q["prices"],
            median=q.get("median"),
            low_data=bool(q.get("low_data")),
            cold_start=bool(q.get("cold_start")),
            n_prior_awards=int(q.get("n_prior_awards", 0)),
            reference_price=q.get("reference_price"),
            reference_type=q.get("reference_type"),
            model=q.get("model"),
            contributions=q.get("contributions", []),
            similar_awards=q.get("similar_awards", []),
            similar_items=_named(ctx, q.get("similar_items") or []),
            created_at=pred.created_at,
        )
        if pred
        else None,
        recommendation=RecommendationOut(
            landed_cost_per_unit=rec.landed_cost_per_unit,
            landed_breakdown=rec.landed_breakdown,
            provisional=rec.provisional,
            recommended_bid=rec.recommended_bid,
            p_win_at_recommended=rec.p_win_at_recommended,
            expected_profit=rec.expected_profit,
            conservative_bid=rec.conservative_bid,
            aggressive_bid=rec.aggressive_bid,
            no_bid=rec.no_bid,
            no_bid_reason=rec.no_bid_reason,
            explanation=rec.explanation or {},
            actual_bid=rec.actual_bid,
            actual_bid_at=rec.actual_bid_at,
            updated_at=rec.updated_at,
        ),
        history=[
            AwardOut(
                award_date=a.award_date,
                contract_number=a.contract_number,
                awardee_cage=a.awardee_cage,
                awardee_name=names.get(a.awardee_cage or ""),
                quantity=a.quantity,
                unit_of_issue=a.unit_of_issue,
                unit_price=a.unit_price,
                solicitation_number=a.solicitation_number,
                source=a.source,
            )
            for a in awards
        ],
        approved_sources=[
            ApprovedSourceOut(cage_code=s.cage_code, part_number=s.part_number,
                              company_name=getattr(db.get(Company, s.cage_code), "name", None))
            for s in sources
        ],
        candidates=candidates,
        outcome=outcome,
    )


@router.get("/{solicitation_number}", response_model=SolicitationDetail)
def rfq_detail(solicitation_number: str, ctx: TenantContext = Depends(get_tenant)) -> SolicitationDetail:
    sn = solicitation_number.replace("-", "").upper()
    sol = ctx.db.execute(
        select(Solicitation).options(selectinload(Solicitation.lines)).where(Solicitation.solicitation_number == sn)
    ).scalar_one_or_none()
    if sol is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitation not found")
    org = ctx.db.get(Organization, ctx.org_id)
    today = today_et()
    return SolicitationDetail(
        id=sol.id,
        solicitation_number=sol.solicitation_number,
        type_code=sol.type_code,
        type_label=TYPE_LABELS.get(sol.type_code or "", sol.type_code or "unknown"),
        issue_date=sol.issue_date,
        return_by_date=sol.return_by_date,
        days_left=(sol.return_by_date - today).days if sol.return_by_date else None,
        buyer_code=sol.buyer_code,
        buyer_name=sol.buyer_name,
        buyer_email=sol.buyer_email,
        buyer_phone=sol.buyer_phone,
        set_aside=sol.set_aside,
        fast_award=sol.fast_award,
        aidc=sol.aidc,
        tech_docs=sol.tech_docs,
        inspection_point=sol.inspection_point,
        fob=sol.fob,
        first_article=sol.first_article,
        naics=sol.naics,
        packaging=sol.packaging,
        status=sol.status,
        pdf_url=sol.pdf_url,
        dibbs_url=dibbs_url(sol.solicitation_number),
        lines=[_line_detail(ctx, ln, org) for ln in sol.lines],
    )


@router.post("/lines/{line_id}/actual-bid", response_model=RecommendationOut)
def record_actual_bid(line_id: int, body: ActualBidIn, ctx: TenantContext = Depends(get_tenant)) -> RecommendationOut:
    line = ctx.db.get(SolicitationLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    rec = ctx.db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == line_id)).scalar_one_or_none()
    if rec is None:
        rec = refresh_line_for_org(ctx.db, ctx.org_id, line)
    rec.actual_bid = round(body.actual_bid, 2) if body.actual_bid is not None else None
    rec.actual_bid_at = datetime.now(UTC) if body.actual_bid is not None else None
    ctx.db.commit()
    return _rec_out(rec)


@router.post("/lines/{line_id}/refresh", response_model=RecommendationOut)
def refresh_recommendation(line_id: int, ctx: TenantContext = Depends(get_tenant)) -> RecommendationOut:
    line = ctx.db.get(SolicitationLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    rec = refresh_line_for_org(ctx.db, ctx.org_id, line)
    ctx.db.commit()
    return _rec_out(rec)


def _rec_out(rec: Recommendation) -> RecommendationOut:
    return RecommendationOut(
        landed_cost_per_unit=rec.landed_cost_per_unit,
        landed_breakdown=rec.landed_breakdown,
        provisional=rec.provisional,
        recommended_bid=rec.recommended_bid,
        p_win_at_recommended=rec.p_win_at_recommended,
        expected_profit=rec.expected_profit,
        conservative_bid=rec.conservative_bid,
        aggressive_bid=rec.aggressive_bid,
        no_bid=rec.no_bid,
        no_bid_reason=rec.no_bid_reason,
        explanation=rec.explanation or {},
        actual_bid=rec.actual_bid,
        actual_bid_at=rec.actual_bid_at,
        updated_at=rec.updated_at,
    )
