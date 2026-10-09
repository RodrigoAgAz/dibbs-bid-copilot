"""Quote requests (email/voice), parsed supplier quotes, review queue."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import get_tenant
from app.api.routes.rfq import quote_request_out
from app.config import get_settings
from app.core.tenancy import TenantContext
from app.models import (
    Channel,
    QuoteRequest,
    QuoteRequestStatus,
    SolicitationLine,
    SupplierContact,
    SupplierQuote,
)
from app.schemas.rfq import (
    CallIn,
    CallLogOut,
    ManualQuoteIn,
    NewQuoteRequestIn,
    QuotePatch,
    QuoteRequestOut,
    QuoteRequestPatch,
    ReviewItem,
    SimulatedReplyIn,
    SupplierQuoteOut,
)
from app.services.recommendations import refresh_line_for_org
from app.services.sourcing import (
    _email_context,
    history_median,
    process_inbound_email,
    regenerate_draft,
    send_quote_request,
)

router = APIRouter(prefix="/api", tags=["quotes"])


def _line(ctx: TenantContext, line_id: int) -> SolicitationLine:
    line = ctx.db.get(SolicitationLine, line_id)
    if line is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    return line


@router.post("/rfq/lines/{line_id}/quote-requests", response_model=QuoteRequestOut, status_code=201)
def add_quote_request(line_id: int, body: NewQuoteRequestIn, ctx: TenantContext = Depends(get_tenant)) -> QuoteRequestOut:
    from app.services.llm import template_email
    from app.services.recommendations import org_settings

    line = _line(ctx, line_id)
    cage = body.cage_code.strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{5}", cage):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "CAGE code must be 5 letters/digits")
    existing = ctx.db.execute(
        select(QuoteRequest).where(QuoteRequest.solicitation_line_id == line_id, QuoteRequest.cage_code == cage,
                                   QuoteRequest.channel == Channel.email)
    ).scalar_one_or_none()
    if existing:
        return quote_request_out(ctx, existing)
    contact = ctx.db.execute(select(SupplierContact).where(SupplierContact.cage_code == cage)).scalars().first()
    qr = ctx.add(
        QuoteRequest(
            solicitation_line_id=line.id,
            cage_code=cage,
            supplier_contact_id=contact.id if contact else None,
            channel=Channel.email,
            status=QuoteRequestStatus.draft,
            candidate_reason="manual",
            part_number=body.part_number,
            reply_token=secrets.token_urlsafe(12),
            email_to=body.email_to or (contact.email if contact else None),
        )
    )
    ctx.db.flush()
    d = template_email(_email_context(ctx.db, qr, line, org_settings(ctx.db, ctx.org_id)))
    qr.email_subject, qr.email_body = d.subject, d.body
    ctx.db.commit()
    return quote_request_out(ctx, qr)


@router.patch("/quote-requests/{qr_id}", response_model=QuoteRequestOut)
def edit_quote_request(qr_id: int, body: QuoteRequestPatch, ctx: TenantContext = Depends(get_tenant)) -> QuoteRequestOut:
    qr = ctx.get(QuoteRequest, qr_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(qr, k, v)
    ctx.db.commit()
    return quote_request_out(ctx, qr)


@router.post("/quote-requests/{qr_id}/draft", response_model=QuoteRequestOut)
def redraft(qr_id: int, ctx: TenantContext = Depends(get_tenant)) -> QuoteRequestOut:
    """(Re)generate the RFQ email with the LLM from the strict template."""
    qr = ctx.get(QuoteRequest, qr_id)
    regenerate_draft(ctx.db, qr)
    ctx.db.commit()
    return quote_request_out(ctx, qr)


@router.post("/quote-requests/{qr_id}/send", response_model=QuoteRequestOut)
def send(qr_id: int, ctx: TenantContext = Depends(get_tenant)) -> QuoteRequestOut:
    qr = ctx.get(QuoteRequest, qr_id)
    if qr.channel != Channel.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only email quote requests can be sent")
    if not qr.email_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add the supplier's email address first")
    send_quote_request(ctx.db, qr, followup=qr.status != QuoteRequestStatus.draft and qr.sent_at is not None)
    ctx.db.commit()
    return quote_request_out(ctx, qr)


@router.post("/quote-requests/{qr_id}/simulate-reply", response_model=QuoteRequestOut)
def simulate_reply(qr_id: int, body: SimulatedReplyIn, ctx: TenantContext = Depends(get_tenant)) -> QuoteRequestOut:
    """Demo mode only: feed a supplier reply through the same inbound pipeline as the webhook."""
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not available")
    qr = ctx.get(QuoteRequest, qr_id)
    result = process_inbound_email(
        ctx.db,
        {"to": f"quotes+{qr.reply_token}@demo", "from": qr.email_to or f"sales@{qr.cage_code.lower()}.example.com",
         "subject": f"RE: {qr.email_subject or 'RFQ'}", "text": body.text},
    )
    if not result.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.get("reason", "could not process reply"))
    ctx.db.commit()
    return quote_request_out(ctx, qr)


@router.post("/quote-requests/{qr_id}/quotes", response_model=SupplierQuoteOut, status_code=201)
def add_manual_quote(qr_id: int, body: ManualQuoteIn, ctx: TenantContext = Depends(get_tenant)) -> SupplierQuote:
    from app.models import ApprovedSource
    from app.services.sourcing import norm_pn

    qr = ctx.get(QuoteRequest, qr_id)
    line = _line(ctx, qr.solicitation_line_id)
    conforms = True
    if line.approved_source_only:
        approved = {norm_pn(a.part_number) for a in ctx.db.execute(select(ApprovedSource).where(ApprovedSource.nsn == line.nsn)).scalars()}
        conforms = norm_pn(body.quoted_part_number or qr.part_number) in approved
    q = ctx.add(SupplierQuote(quote_request_id=qr.id, source="manual", extraction_confidence=1.0,
                              conforms_to_part_number=conforms, **body.model_dump()))
    qr.status = QuoteRequestStatus.parsed
    ctx.db.flush()
    refresh_line_for_org(ctx.db, ctx.org_id, line)
    ctx.db.commit()
    return q


@router.patch("/supplier-quotes/{quote_id}", response_model=SupplierQuoteOut)
def edit_quote(quote_id: int, body: QuotePatch, ctx: TenantContext = Depends(get_tenant)) -> SupplierQuote:
    q = ctx.get(SupplierQuote, quote_id)
    data = body.model_dump(exclude_unset=True)
    mark = data.pop("mark_reviewed", False)
    for k, v in data.items():
        setattr(q, k, v)
    if mark or data:
        q.reviewed_at = datetime.now(UTC)
        q.needs_review = False
    qr = ctx.get(QuoteRequest, q.quote_request_id)
    refresh_line_for_org(ctx.db, ctx.org_id, _line(ctx, qr.solicitation_line_id))
    ctx.db.commit()
    return q


@router.get("/review-queue", response_model=list[ReviewItem])
def review_queue(ctx: TenantContext = Depends(get_tenant)) -> list[ReviewItem]:
    return build_review_items(ctx.db)


def build_review_items(db) -> list[ReviewItem]:
    from app.models import Organization

    rows = db.execute(
        select(SupplierQuote, QuoteRequest, SolicitationLine)
        .join(QuoteRequest, SupplierQuote.quote_request_id == QuoteRequest.id)
        .join(SolicitationLine, QuoteRequest.solicitation_line_id == SolicitationLine.id)
        .where(SupplierQuote.needs_review.is_(True), SupplierQuote.reviewed_at.is_(None))
        .order_by(SupplierQuote.parsed_at.desc())
    ).all()
    out = []
    for q, qr, line in rows:
        org = db.get(Organization, q.organization_id)
        out.append(
            ReviewItem(
                quote=SupplierQuoteOut.model_validate(q),
                organization_id=q.organization_id,
                organization_name=org.name if org else None,
                cage_code=qr.cage_code,
                solicitation_number=line.solicitation.solicitation_number,
                nsn=line.nsn,
                nomenclature=line.nomenclature,
                quantity=line.quantity,
                history_median=history_median(db, line.nsn),
            )
        )
    return out


@router.post("/rfq/lines/{line_id}/call", response_model=CallLogOut, status_code=201)
def call(line_id: int, body: CallIn, ctx: TenantContext = Depends(get_tenant)):
    from app.services.voice.calls import call_supplier

    line = _line(ctx, line_id)
    contact = ctx.get(SupplierContact, body.supplier_contact_id)
    log_row = call_supplier(ctx.db, ctx.org_id, line, contact, body.part_number)
    ctx.db.commit()
    return CallLogOut.model_validate(log_row)
