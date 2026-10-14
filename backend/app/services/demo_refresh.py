"""Keep the public demo alive on real solicitations (orgs with plan "demo" only).

Every morning the demo org gets simulated supplier quotes on a handful of its best real open lines, so a visitor
sees the whole loop on today's DIBBS data: RFQ emails out, replies parsed, landed cost firmed up, bid
re-optimized, a doubtful quote waiting in the review queue.

Everything simulated is fictional and labeled: the suppliers are made-up companies named "(demo)" with
placeholder CAGE codes and example.com addresses, so no real company is ever shown quoting a price. Emails go
through the console provider (recorded, never sent). Real customer organizations are never touched.
"""

from __future__ import annotations

import logging
import random
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.timeutil import today_et
from app.models import (
    Channel,
    Organization,
    QuoteRequest,
    QuoteRequestStatus,
    Recommendation,
    Solicitation,
    SolicitationLine,
    SupplierContact,
)
from app.services.recommendations import org_settings

log = logging.getLogger(__name__)

TARGET_QUOTED_LINES = 12
QUOTES_PER_LINE = 3
DEMO_SUPPLIERS = [
    ("ZZD01", "Harbor Fastener Supply (demo)", "Dana Lee"),
    ("ZZD02", "Keystone Industrial Parts (demo)", "Jordan Patel"),
    ("ZZD03", "Northwind Seal & Gasket (demo)", "Morgan Garcia"),
    ("ZZD04", "Summit Wire & Cable (demo)", "Casey Nguyen"),
    ("ZZD05", "Bayline Hardware Distributors (demo)", "Riley Smith"),
    ("ZZD06", "Granite State Fittings (demo)", "Taylor Kim"),
]


def ensure_demo_suppliers(db: Session, org_id: int) -> list[SupplierContact]:
    fscs = list(org_settings(db, org_id).fsc_watchlist or [])
    out = []
    for cage, name, person in DEMO_SUPPLIERS:
        c = db.execute(
            select(SupplierContact).where(SupplierContact.organization_id == org_id, SupplierContact.cage_code == cage)
        ).scalar_one_or_none()
        if c is None:
            slug = "".join(ch for ch in name.lower() if ch.isalnum())[:20]
            c = SupplierContact(organization_id=org_id, cage_code=cage, company_name=name, name=person,
                                email=f"quotes@{slug}.example.com", notes="Fictional demo supplier")
            db.add(c)
        c.fsc_tags = fscs  # a "saved supplier" candidate on every line the demo org watches
        out.append(c)
    db.flush()
    return out


def _draft(db: Session, org_id: int, line: SolicitationLine, s, contact: SupplierContact) -> QuoteRequest:
    from app.services.llm import template_email
    from app.services.sourcing import _email_context

    qr = QuoteRequest(organization_id=org_id, solicitation_line_id=line.id, cage_code=contact.cage_code,
                      supplier_contact_id=contact.id, channel=Channel.email, status=QuoteRequestStatus.draft,
                      candidate_reason="saved", reply_token=secrets.token_urlsafe(12), email_to=contact.email)
    db.add(qr)
    db.flush()
    d = template_email(_email_context(db, qr, line, s))
    qr.email_subject, qr.email_body = d.subject, d.body
    return qr


def _reply(kind: str, price: float, pn: str | None, rng: random.Random) -> str:
    pn = pn or "per NSN"
    if kind == "good":
        return (f"Hi,\n\nThanks for the RFQ. We can supply P/N {pn}.\nUnit price: ${price:.2f} each, lead time "
                f"{rng.randint(1, 6)} weeks ARO. Quote valid for 30 days. Freight prepaid and add. CoC included."
                "\n\nBest regards,\nSales")
    if kind == "detailed":
        return (f"Quote for P/N {pn}: ${price:.2f}/ea, MOQ {rng.randint(1, 25)}, delivery {rng.randint(5, 20)} business "
                f"days. FOB origin, shipping ${rng.randint(15, 60)}.00. Certificate of conformance available.")
    if kind == "ambiguous":
        return (f"We have these in stock. Pricing is about ${price * 1.1:.2f} for small lots or ${price * 0.92:.2f} "
                "for larger quantities. Let me know what works.")
    return "Sorry, we are unable to quote this item at this time."


def refresh_demo_org(db: Session, org: Organization, rng: random.Random | None = None) -> dict:
    """Top the demo org up to TARGET_QUOTED_LINES open lines with simulated supplier quotes."""
    from app.services.sourcing import process_inbound_email, send_quote_request

    rng = rng or random.Random(today_et().toordinal())
    s = org_settings(db, org.id)
    suppliers = ensure_demo_suppliers(db, org.id)
    today = today_et()
    open_quoted = {
        lid for (lid,) in db.execute(
            select(QuoteRequest.solicitation_line_id)
            .join(SolicitationLine, QuoteRequest.solicitation_line_id == SolicitationLine.id)
            .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
            .where(QuoteRequest.organization_id == org.id, Solicitation.return_by_date >= today)
        ).all()
    }
    need = TARGET_QUOTED_LINES - len(open_quoted)
    if need <= 0:
        return {"org": org.id, "lines": 0, "replies": 0}
    rows = db.execute(
        select(Recommendation, SolicitationLine)
        .join(SolicitationLine, Recommendation.solicitation_line_id == SolicitationLine.id)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .where(
            Recommendation.organization_id == org.id,
            Recommendation.no_bid.is_(False),
            Recommendation.recommended_bid.is_not(None),
            Solicitation.return_by_date >= today + timedelta(days=2),
            ~Solicitation.solicitation_number.startswith("SPEZZ"),  # real DIBBS lines only
            SolicitationLine.fsc.in_(list(s.fsc_watchlist or [])),
            SolicitationLine.approved_source_only.is_(False),  # fictional suppliers can't be approved sources
        )
    ).all()
    picks = [
        (rec, ln) for rec, ln in rows
        if ln.id not in open_quoted and (rec.explanation or {}).get("confidence") in ("high", "medium")
    ]
    picks.sort(key=lambda r: -((r[0].explanation or {}).get("rank_score") or 0))
    replies = 0
    reply_domain = get_settings().email_reply_domain
    for rec, ln in picks[:need]:
        median = float(((rec.explanation or {}).get("distribution") or {}).get("median") or 0)
        if median <= 0:
            continue
        for contact in rng.sample(suppliers, QUOTES_PER_LINE):
            qr = _draft(db, org.id, ln, s, contact)
            send_quote_request(db, qr)
            qr.sent_at = datetime.now(UTC) - timedelta(hours=rng.randint(2, 40))
            if rng.random() < 0.2:
                continue  # still waiting for this supplier
            kind = rng.choices(["good", "detailed", "ambiguous", "decline"], weights=[55, 25, 8, 12])[0]
            price = median * rng.uniform(0.55, 0.85)
            process_inbound_email(db, {"to": f"quotes+{qr.reply_token}@{reply_domain}", "from": qr.email_to,
                                       "subject": f"RE: {qr.email_subject}", "text": _reply(kind, price, qr.part_number, rng)})
            replies += 1
        db.flush()
    db.commit()
    return {"org": org.id, "lines": min(need, len(picks)), "replies": replies}


def refresh_demo_orgs(db: Session) -> list[dict]:
    out = []
    for org in db.execute(select(Organization).where(Organization.plan == "demo")).scalars().all():
        try:
            out.append(refresh_demo_org(db, org))
        except Exception:
            db.rollback()
            log.exception("demo refresh failed for org %s", org.id)
    log.info("demo refresh: %s", out)
    return out
