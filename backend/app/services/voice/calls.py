"""Place a supplier call for a quote (guardrails first), store the call log, extract the quote."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import today_et
from app.models import (
    CallLog,
    Channel,
    Company,
    Organization,
    QuoteRequest,
    QuoteRequestStatus,
    SolicitationLine,
    SupplierContact,
)
from app.services.llm import ExtractionContext, get_llm
from app.services.pricing import latest_prediction
from app.services.sourcing import store_extraction
from app.services.voice.guardrails import check_call_allowed
from app.services.voice.providers import CallRequest, VoiceProvider, get_voice_provider


def _voice_request(db: Session, org_id: int, line: SolicitationLine, contact: SupplierContact,
                   part_number: str | None) -> QuoteRequest:
    qr = db.execute(
        select(QuoteRequest).where(
            QuoteRequest.organization_id == org_id,
            QuoteRequest.solicitation_line_id == line.id,
            QuoteRequest.cage_code == contact.cage_code,
            QuoteRequest.channel == Channel.voice,
        )
    ).scalar_one_or_none()
    if qr is None:
        qr = QuoteRequest(
            organization_id=org_id,
            solicitation_line_id=line.id,
            cage_code=contact.cage_code,
            supplier_contact_id=contact.id,
            channel=Channel.voice,
            status=QuoteRequestStatus.draft,
            candidate_reason="voice",
            part_number=part_number,
            reply_token=secrets.token_urlsafe(12),
            email_to=contact.email,
        )
        db.add(qr)
        db.flush()
    return qr


def call_supplier(
    db: Session,
    org_id: int,
    line: SolicitationLine,
    contact: SupplierContact,
    part_number: str | None = None,
    provider: VoiceProvider | None = None,
    now: datetime | None = None,
) -> CallLog:
    guard = check_call_allowed(db, org_id, contact, now)
    if not guard.allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Call not allowed", "reasons": guard.reasons})
    provider = provider or get_voice_provider()
    qr = _voice_request(db, org_id, line, contact, part_number)
    org = db.get(Organization, org_id)
    comp = db.get(Company, contact.cage_code)
    pred = latest_prediction(db, line.id)
    req = CallRequest(
        quote_request_id=qr.id,
        to_number=contact.phone or "",
        broker_name=org.name if org else "our company",
        supplier_name=contact.company_name or (comp.name if comp else contact.cage_code),
        part_number=part_number or qr.part_number or "per NSN",
        nsn=line.nsn,
        quantity=line.quantity,
        unit=line.unit_of_issue or "EA",
        price_hint=(pred.quantiles.get("median") if pred else None),
        seed=f"{org_id}:{contact.cage_code}",
    )
    result = provider.place_call(req)
    started = now or datetime.now(UTC)
    log_row = CallLog(
        organization_id=org_id,
        quote_request_id=qr.id,
        cage_code=contact.cage_code,
        provider=result.provider,
        provider_call_id=result.provider_call_id,
        to_number=contact.phone,
        started_at=started,
        ended_at=started if result.status == "completed" else None,
        disclosure_played=result.disclosure_played,
        transcript=result.transcript,
        recording_url=result.recording_url,
        outcome=result.outcome,
    )
    db.add(log_row)
    qr.sent_at = started
    qr.status = QuoteRequestStatus.sent if result.status != "failed" else QuoteRequestStatus.failed
    qr.error = result.error
    db.flush()
    if result.transcript:
        finish_call(db, log_row, result.transcript)
    return log_row


def finish_call(db: Session, log_row: CallLog, transcript: str) -> None:
    """Run the same LLM extraction as for email replies on a call transcript."""
    qr = db.get(QuoteRequest, log_row.quote_request_id)
    line = db.get(SolicitationLine, qr.solicitation_line_id)
    log_row.transcript = transcript
    log_row.disclosure_played = log_row.disclosure_played or "AI assistant calling on behalf of" in transcript
    log_row.ended_at = log_row.ended_at or datetime.now(UTC)
    qr.replied_at = log_row.ended_at
    # Only the supplier's words count as the quote.
    supplier_text = "\n".join(
        ln.split(":", 1)[1].strip() for ln in transcript.splitlines() if ln.upper().startswith(("SUPPLIER:", "USER:"))
    ) or transcript
    ctx = ExtractionContext(nsn=line.nsn, part_numbers=[qr.part_number] if qr.part_number else [],
                            quantity=line.quantity, unit_of_issue=line.unit_of_issue or "EA", today=today_et())
    ext = get_llm().extract_quote(supplier_text, ctx)
    store_extraction(db, qr, ext, transcript, "voice")
    if log_row.outcome in (None, "queued"):
        log_row.outcome = "declined" if ext.declined else ("quoted" if ext.is_quote else "no_quote")
    from app.services.recommendations import refresh_line_for_org

    refresh_line_for_org(db, qr.organization_id, line)
