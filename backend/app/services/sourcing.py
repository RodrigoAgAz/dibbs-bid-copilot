"""Supplier sourcing agent: who to ask, RFQ emails, inbound reply parsing, follow-ups.

Candidate order for a line: (1) approved sources (manufacturer CAGE + P/N) from the RFQ,
(2) historical awardees of the NSN, (3) suppliers the organization saved for the FSC.
For approved-source-only lines, only quotes for an approved part number count toward the cost floor.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import secrets
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import today_et
from app.models import (
    ApprovedSource,
    AwardHistory,
    Channel,
    Company,
    EmailMessage,
    Organization,
    OrgSettings,
    QuoteRequest,
    QuoteRequestStatus,
    SolicitationLine,
    SupplierContact,
    SupplierQuote,
)
from app.services.email.providers import OutboundEmail, get_email_provider, reply_address
from app.services.llm import ExtractionContext, QuoteExtraction, RfqEmailContext, get_llm, template_email

log = logging.getLogger(__name__)

MAX_CANDIDATES = 10
REVIEW_CONFIDENCE = 0.6
REVIEW_PRICE_RATIO = 5.0
FOLLOWUP_AFTER = timedelta(hours=48)
TOKEN_RE = re.compile(r"quotes\+([A-Za-z0-9_\-]{8,64})@", re.I)


def norm_pn(pn: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (pn or "").upper())


@dataclass
class Candidate:
    cage_code: str
    reason: str  # approved_source | historical | saved
    part_numbers: list[str]
    contact: SupplierContact | None
    company_name: str | None
    awards: int = 0


def candidate_suppliers(db: Session, org_id: int, line: SolicitationLine) -> list[Candidate]:
    out: dict[str, Candidate] = {}
    contacts = {
        c.cage_code: c
        for c in db.execute(select(SupplierContact).where(SupplierContact.organization_id == org_id)).scalars()
    }

    def company(cage: str) -> str | None:
        comp = db.get(Company, cage)
        return comp.name if comp else None

    for src in db.execute(select(ApprovedSource).where(ApprovedSource.nsn == line.nsn)).scalars():
        cand = out.get(src.cage_code)
        if cand:
            cand.part_numbers.append(src.part_number)
        else:
            out[src.cage_code] = Candidate(src.cage_code, "approved_source", [src.part_number],
                                           contacts.get(src.cage_code), company(src.cage_code))
    hist = db.execute(
        select(AwardHistory.awardee_cage, func.count())
        .where(AwardHistory.nsn == line.nsn, AwardHistory.awardee_cage.is_not(None))
        .group_by(AwardHistory.awardee_cage)
        .order_by(func.count().desc())
        .limit(6)
    ).all()
    for cage, n in hist:
        if cage in out:
            out[cage].awards = int(n)
        else:
            out[cage] = Candidate(cage, "historical", [], contacts.get(cage), company(cage), awards=int(n))
    for cage, c in contacts.items():
        if cage not in out and line.fsc in (c.fsc_tags or []):
            out[cage] = Candidate(cage, "saved", [], c, c.company_name or company(cage))
    # Never the broker's own company (it shows up as a past awardee once they win).
    org = db.get(Organization, org_id)
    own = (org.cage_code or "").upper() if org else ""
    if own:
        out.pop(own, None)
    return list(out.values())[:MAX_CANDIDATES]


def _email_context(db: Session, qr: QuoteRequest, line: SolicitationLine, s: OrgSettings,
                   followup: bool = False) -> RfqEmailContext:
    org = db.get(Organization, qr.organization_id)
    sol = line.solicitation
    contact = db.get(SupplierContact, qr.supplier_contact_id) if qr.supplier_contact_id else None
    comp = db.get(Company, qr.cage_code)
    approved = [qr.part_number] if qr.part_number else [
        a.part_number for a in db.execute(select(ApprovedSource).where(ApprovedSource.nsn == line.nsn)).scalars()
    ][:3]
    base = sol.return_by_date or today_et()
    deliver_by = base + timedelta(days=int(line.delivery_days or 60))
    certs = ["Certificate of Conformance (CoC)"]
    if line.approved_source_only:
        certs.append("traceability to the approved manufacturer")
    return RfqEmailContext(
        broker_name=org.name if org else "Our company",
        sender_name=s.email_sender_name or (org.name if org else "Purchasing"),
        supplier_name=(contact.company_name if contact and contact.company_name else None) or (comp.name if comp and comp.name else f"CAGE {qr.cage_code}"),
        contact_name=contact.name if contact else None,
        solicitation_number=sol.solicitation_number,
        nsn=line.nsn,
        nomenclature=line.nomenclature or "",
        part_numbers=approved,
        quantity=line.quantity,
        unit_of_issue=line.unit_of_issue or "EA",
        required_delivery_date=deliver_by.strftime("%B %d, %Y"),
        fob=sol.fob or "Destination",
        inspection_point=sol.inspection_point or "Destination",
        packaging=sol.packaging or "Commercial packaging; MIL-STD-129 marking per the solicitation",
        certs_required=certs,
        approved_source_only=line.approved_source_only,
        signature=s.email_signature,
        followup=followup,
    )


def create_draft_requests(db: Session, org_id: int, line: SolicitationLine, s: OrgSettings,
                          cage_codes: set[str] | None = None, only_sendable: bool = False,
                          allow_auto_send: bool = True) -> list[QuoteRequest]:
    """Draft one email quote request per candidate supplier (idempotent). Auto-sends if enabled.

    `cage_codes` limits it to those candidates; `only_sendable` skips candidates without an email address
    (used by the automatic path, where a draft nobody can send is just noise).
    """
    created = []
    existing = {
        qr.cage_code
        for qr in db.execute(
            select(QuoteRequest).where(
                QuoteRequest.organization_id == org_id,
                QuoteRequest.solicitation_line_id == line.id,
                QuoteRequest.channel == Channel.email,
            )
        ).scalars()
    }
    for cand in candidate_suppliers(db, org_id, line):
        if cand.cage_code in existing or (cage_codes is not None and cand.cage_code not in cage_codes):
            continue
        if only_sendable and not (cand.contact and cand.contact.email):
            continue
        qr = QuoteRequest(
            organization_id=org_id,
            solicitation_line_id=line.id,
            cage_code=cand.cage_code,
            supplier_contact_id=cand.contact.id if cand.contact else None,
            channel=Channel.email,
            status=QuoteRequestStatus.draft,
            candidate_reason=cand.reason,
            part_number=cand.part_numbers[0] if cand.part_numbers else None,
            reply_token=secrets.token_urlsafe(12),
            email_to=cand.contact.email if cand.contact else None,
        )
        db.add(qr)
        db.flush()
        # Drafts use the deterministic template; the LLM polishes on demand or before auto-send.
        draft = template_email(_email_context(db, qr, line, s))
        qr.email_subject, qr.email_body = draft.subject, draft.body
        created.append(qr)
        if allow_auto_send and s.auto_send_rfq and qr.email_to:
            regenerate_draft(db, qr)
            send_quote_request(db, qr)
    return created


def regenerate_draft(db: Session, qr: QuoteRequest) -> QuoteRequest:
    line = db.get(SolicitationLine, qr.solicitation_line_id)
    s = db.get(OrgSettings, qr.organization_id)
    draft = get_llm().draft_rfq_email(_email_context(db, qr, line, s))
    qr.email_subject, qr.email_body = draft.subject, draft.body
    return qr


def send_quote_request(db: Session, qr: QuoteRequest, followup: bool = False) -> QuoteRequest:
    if not qr.email_to:
        qr.error = "No email address for this supplier. Add one in Suppliers."
        return qr
    subject, body = qr.email_subject, qr.email_body
    if followup:
        line = db.get(SolicitationLine, qr.solicitation_line_id)
        s = db.get(OrgSettings, qr.organization_id)
        d = template_email(_email_context(db, qr, line, s, followup=True))
        subject, body = d.subject, d.body
    result = get_email_provider().send(
        db,
        OutboundEmail(
            to=qr.email_to,
            subject=subject or "Request for quote",
            body=body or "",
            reply_to=reply_address(qr.reply_token),
            organization_id=qr.organization_id,
            quote_request_id=qr.id,
            kind="followup" if followup else "rfq",
        ),
    )
    now = datetime.now(UTC)
    if result.ok:
        if followup:
            qr.followup_count += 1
            qr.last_followup_at = now
        else:
            qr.status = QuoteRequestStatus.sent
            qr.sent_at = now
        qr.error = None
    else:
        qr.status = QuoteRequestStatus.failed if not followup else qr.status
        qr.error = result.error
    return qr


def send_quote_request_by_id(db: Session, qr_id: int) -> dict:
    qr = db.get(QuoteRequest, qr_id)
    if qr is None:
        return {"ok": False, "error": "not found"}
    send_quote_request(db, qr)
    return {"ok": qr.error is None, "status": qr.status.value}


# ----------------------------------------------------------------------------- inbound
def _attachment_text(att: dict) -> str:
    name = (att.get("filename") or "").lower()
    ctype = (att.get("content_type") or "").lower()
    raw = att.get("content_base64")
    if not raw:
        return ""
    try:
        data = base64.b64decode(raw)
    except Exception:
        return ""
    if name.endswith(".pdf") or "pdf" in ctype:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return "\n".join((p.extract_text() or "") for p in pdf.pages[:10])
        except Exception:
            log.warning("could not read PDF attachment %s", name)
            return ""
    if ctype.startswith("text/") or name.endswith((".txt", ".csv")):
        return data.decode("utf-8", errors="replace")[:20000]
    return ""


def history_median(db: Session, nsn: str) -> float | None:
    prices = [p for (p,) in db.execute(select(AwardHistory.unit_price).where(AwardHistory.nsn == nsn)).all() if p and p > 0]
    return statistics.median(prices) if prices else None


EVIDENCE_KEYS = ("part_number", "unit", "lead_time", "validity", "freight")


def quote_evidence(ext: QuoteExtraction, unit_evidence: str, part_number_inferred: bool = False) -> dict:
    """What the supplier actually confirmed, per fact: confirmed | inferred | unknown.

    A fact is "confirmed" only when the supplier stated it. "inferred" means the tool filled it in (the requested
    part number on a manual entry, or a price assumed to be per unit of issue), and the broker should see that.
    """
    return {
        "part_number": "confirmed" if ext.quoted_part_number else ("inferred" if part_number_inferred else "unknown"),
        "unit": unit_evidence if ext.unit_price is not None else "unknown",
        "lead_time": "confirmed" if ext.lead_time_days is not None else "unknown",
        "validity": "confirmed" if ext.quote_valid_until else "unknown",
        "freight": "confirmed" if (ext.freight_cost is not None or ext.freight_terms) else "unknown",
    }


def store_extraction(
    db: Session, qr: QuoteRequest, ext: QuoteExtraction, raw_text: str, source: str
) -> SupplierQuote:
    from app.services.landed_cost import price_per_issue_unit

    line = db.get(SolicitationLine, qr.solicitation_line_id)
    approved = {norm_pn(a.part_number) for a in db.execute(select(ApprovedSource).where(ApprovedSource.nsn == line.nsn)).scalars()}
    reasons = []
    if line.approved_source_only:
        # Only a part number the supplier themselves stated can conform. Falling back to the part number we asked for
        # would mark a vague "yes, $4.20 each" as a conforming approved-source quote.
        quoted = norm_pn(ext.quoted_part_number)
        conforms = bool(quoted) and quoted in approved
        if ext.is_quote and not quoted:
            reasons.append("supplier did not state the part number (approved-source-only line)")
    else:
        conforms = True
    if ext.extraction_confidence < REVIEW_CONFIDENCE:
        reasons.append(f"low extraction confidence ({ext.extraction_confidence:.2f})")
    per_unit, unit_problem, unit_evidence = price_per_issue_unit(ext, line.unit_of_issue)
    if ext.unit_price is not None and unit_problem:
        reasons.append(f"pricing unit: {unit_problem}")
    med = history_median(db, line.nsn)
    if per_unit and med:
        ratio = per_unit / med
        if ratio > REVIEW_PRICE_RATIO or ratio < 1 / REVIEW_PRICE_RATIO:
            reasons.append(f"price ${per_unit:,.4f} per {line.unit_of_issue or 'EA'} is {ratio:.1f}x the historical median ${med:,.2f}")
    if line.approved_source_only and not conforms and ext.quoted_part_number:
        reasons.append("part number does not match an approved source (non-conforming)")
    quote = SupplierQuote(
        organization_id=qr.organization_id,
        quote_request_id=qr.id,
        unit_price=ext.unit_price,
        currency=ext.currency or "USD",
        price_unit=(ext.price_unit or None),
        price_basis_qty=ext.price_basis_qty,
        evidence=quote_evidence(ext, unit_evidence),
        quoted_part_number=ext.quoted_part_number,
        min_order_qty=ext.min_order_qty,
        lead_time_days=ext.lead_time_days,
        quote_valid_until=ext.quote_valid_until,
        freight_terms=ext.freight_terms,
        freight_cost=ext.freight_cost,
        certs_offered=ext.certs_offered,
        conforms_to_part_number=conforms,
        declined=ext.declined,
        raw_text=raw_text[:20000],
        extraction_confidence=ext.extraction_confidence,
        needs_review=bool(reasons) and not ext.declined,
        review_reason="; ".join(reasons) if reasons else None,
        source=source,
    )
    db.add(quote)
    if ext.declined:
        qr.status = QuoteRequestStatus.declined
    elif ext.is_quote:
        qr.status = QuoteRequestStatus.parsed
    else:
        qr.status = QuoteRequestStatus.replied
    db.flush()
    return quote


def process_inbound_email(db: Session, payload: dict) -> dict:
    """Map a reply to its quote request by the reply-to token, store it, extract the quote."""
    addrs = " ".join(str(payload.get(k) or "") for k in ("to", "cc", "recipient", "envelope_to"))
    m = TOKEN_RE.search(addrs)
    if not m:
        return {"ok": False, "reason": "no reply token in recipient address"}
    qr = db.execute(select(QuoteRequest).where(QuoteRequest.reply_token == m.group(1))).scalar_one_or_none()
    if qr is None:
        return {"ok": False, "reason": "unknown reply token"}
    text = str(payload.get("text") or "")
    if not text and payload.get("html"):
        from bs4 import BeautifulSoup

        text = BeautifulSoup(str(payload["html"]), "lxml").get_text("\n")
    attachments = payload.get("attachments") or []
    att_text = "\n\n".join(t for t in (_attachment_text(a) for a in attachments) if t)
    db.add(
        EmailMessage(
            organization_id=qr.organization_id,
            quote_request_id=qr.id,
            direction="inbound",
            kind="reply",
            provider="webhook",
            provider_message_id=payload.get("message_id"),
            from_addr=str(payload.get("from") or "")[:320],
            to_addr=addrs[:320],
            subject=str(payload.get("subject") or "")[:500],
            body=text[:50000],
            attachments=[{"filename": a.get("filename"), "content_type": a.get("content_type")} for a in attachments] or None,
        )
    )
    qr.replied_at = datetime.now(UTC)
    qr.status = QuoteRequestStatus.replied
    line = db.get(SolicitationLine, qr.solicitation_line_id)
    full = text + (f"\n\n[Attachment text]\n{att_text}" if att_text else "")
    ctx = ExtractionContext(
        nsn=line.nsn,
        part_numbers=[qr.part_number] if qr.part_number else [],
        quantity=line.quantity,
        unit_of_issue=line.unit_of_issue or "EA",
        today=today_et(),
    )
    try:
        ext = get_llm().extract_quote(full, ctx)
    except Exception as exc:
        log.exception("quote extraction failed for QR %s", qr.id)
        ext = QuoteExtraction(is_quote=False, extraction_confidence=0.0, notes=f"extraction failed: {exc}"[:300])
    quote = store_extraction(db, qr, ext, full, "email")
    from app.services.recommendations import refresh_line_for_org

    refresh_line_for_org(db, qr.organization_id, line)
    return {"ok": True, "quote_request_id": qr.id, "supplier_quote_id": quote.id, "needs_review": quote.needs_review,
            "status": qr.status.value}


# ----------------------------------------------------------------------------- follow-ups
def run_followups(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    today: date = today_et()
    followed, expired = 0, 0
    rows = db.execute(
        select(QuoteRequest).where(QuoteRequest.status == QuoteRequestStatus.sent, QuoteRequest.channel == Channel.email)
    ).scalars().all()
    for qr in rows:
        line = db.get(SolicitationLine, qr.solicitation_line_id)
        due = line.solicitation.return_by_date if line else None
        if due and due < today:
            qr.status = QuoteRequestStatus.no_response
            expired += 1
        elif qr.followup_count == 0 and qr.sent_at and now - qr.sent_at >= FOLLOWUP_AFTER:
            send_quote_request(db, qr, followup=True)
            followed += 1
    db.flush()
    return {"followups_sent": followed, "marked_no_response": expired}
