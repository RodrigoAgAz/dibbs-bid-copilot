"""Supplier sourcing: candidates, drafts, sending, signed inbound replies, extraction, review queue, follow-ups."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.routes.webhooks import sign_inbound
from app.models import (
    ApprovedSource,
    EmailMessage,
    QuoteRequest,
    QuoteRequestStatus,
    Recommendation,
    SolicitationLine,
    SupplierContact,
    SupplierQuote,
)
from app.services.sourcing import candidate_suppliers, run_followups
from tests.conftest import csrf, signup
from tests.helpers import build_world


def _setup(client, db):
    data, line_ids = build_world(db)
    me = signup(client, "buyer@broker.example.com", org="Broker One")
    org_id = me["organization"]["id"]
    return data, line_ids, org_id


def _line_with_sources(db, line_ids, aso: bool | None = None):
    for lid in line_ids:
        ln = db.get(SolicitationLine, lid)
        has = db.execute(select(ApprovedSource).where(ApprovedSource.nsn == ln.nsn)).first()
        if has and (aso is None or ln.approved_source_only == aso):
            return ln
    raise AssertionError("no suitable line")


def _draft(client, db, ln) -> QuoteRequest:
    """Quote requests are drafted on demand from the RFQ page (none exist right after signup)."""
    assert db.execute(select(QuoteRequest).where(QuoteRequest.solicitation_line_id == ln.id)).first() is None
    r = client.post(f"/api/rfq/lines/{ln.id}/quote-requests/draft", json={}, headers=csrf(client))
    assert r.status_code == 201 and r.json(), r.text
    db.expire_all()
    return db.get(QuoteRequest, r.json()[0]["id"])


def test_on_demand_drafts_are_idempotent_and_filterable(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids)
    cands = candidate_suppliers(db, org_id, ln)
    first = cands[0].cage_code
    r = client.post(f"/api/rfq/lines/{ln.id}/quote-requests/draft", json={"cage_codes": [first]}, headers=csrf(client))
    assert r.status_code == 201 and [q["cage_code"] for q in r.json()] == [first]
    r = client.post(f"/api/rfq/lines/{ln.id}/quote-requests/draft", json={}, headers=csrf(client))
    assert {q["cage_code"] for q in r.json()} == {c.cage_code for c in cands} - {first}
    r = client.post(f"/api/rfq/lines/{ln.id}/quote-requests/draft", json={}, headers=csrf(client))
    assert r.json() == []  # nothing left to draft
    assert all(q.status == QuoteRequestStatus.draft for q in db.execute(select(QuoteRequest)).scalars())


def test_candidate_order_approved_then_historical_then_saved(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids)
    db.add(SupplierContact(organization_id=org_id, cage_code="7SAVE", email="s@save.example.com", fsc_tags=[ln.fsc]))
    db.commit()
    cands = candidate_suppliers(db, org_id, ln)
    reasons = [c.reason for c in cands]
    assert reasons[0] == "approved_source"
    assert reasons == sorted(reasons, key=["approved_source", "historical", "saved"].index)
    assert any(c.cage_code == "7SAVE" and c.reason == "saved" for c in cands)


def test_send_email_and_signed_inbound_reply_updates_recommendation(client, app, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids, aso=False)
    qr = _draft(client, db, ln)
    assert qr is not None and qr.status == QuoteRequestStatus.draft and "RFQ" in qr.email_subject
    r = client.patch(f"/api/quote-requests/{qr.id}", json={"email_to": "sales@supplier.example.com"}, headers=csrf(client))
    assert r.status_code == 200
    r = client.post(f"/api/quote-requests/{qr.id}/send", headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "sent"
    out = db.execute(select(EmailMessage).where(EmailMessage.quote_request_id == qr.id)).scalars().one()
    assert out.reply_to == f"quotes+{qr.reply_token}@copilot.example.com" and "NSN" in out.body

    rec_before = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == ln.id)).scalar_one()
    assert rec_before.provisional
    payload = {
        "from": "sales@supplier.example.com",
        "to": f"Quotes <quotes+{qr.reply_token}@copilot.example.com>",
        "subject": "RE: RFQ",
        "text": "Hi, unit price: $3.25 each, lead time 3 weeks ARO, quote valid for 30 days. Freight prepaid.\n\n> original request quoted here $999.00",
    }
    raw = json.dumps(payload).encode()
    # Bad signature rejected.
    assert client.post("/api/webhooks/inbound-email", content=raw, headers={"x-inbound-signature": "bad"}).status_code == 401
    r = client.post("/api/webhooks/inbound-email", content=raw,
                    headers={"x-inbound-signature": sign_inbound("test-inbound-secret", raw), "content-type": "application/json"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    db.expire_all()
    q = db.execute(select(SupplierQuote).where(SupplierQuote.quote_request_id == qr.id)).scalar_one()
    assert q.unit_price == 3.25 and q.lead_time_days == 21 and q.freight_terms.lower().startswith("prepaid")
    rec = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == ln.id)).scalar_one()
    if not q.needs_review:
        assert not rec.provisional and rec.best_quote_id == q.id
        assert rec.landed_breakdown["supplier_unit_price"] == 3.25


def test_review_queue_for_low_confidence_and_outliers(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids, aso=False)
    qr = _draft(client, db, ln)
    r = client.post(f"/api/quote-requests/{qr.id}/simulate-reply", headers=csrf(client),
                    json={"text": "We have it. About $1.10 for small lots or $0.90 for bigger ones."})
    assert r.status_code == 200
    queue = client.get("/api/review-queue").json()
    assert any("confidence" in (i["quote"]["review_reason"] or "") for i in queue)
    item = queue[0]
    r = client.patch(f"/api/supplier-quotes/{item['quote']['id']}", json={"unit_price": 1.0, "mark_reviewed": True},
                     headers=csrf(client))
    assert r.status_code == 200 and r.json()["needs_review"] is False
    assert client.get("/api/review-queue").json() == []


def test_aso_line_non_conforming_quote_is_flagged(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids, aso=True) if any(
        db.get(SolicitationLine, i).approved_source_only for i in line_ids) else None
    if ln is None:
        return
    qr = _draft(client, db, ln)
    client.post(f"/api/quote-requests/{qr.id}/simulate-reply", headers=csrf(client),
                json={"text": "We can offer an equivalent, P/N ALT-12345, unit price $2.00 each, 2 weeks."})
    q = db.execute(select(SupplierQuote).where(SupplierQuote.quote_request_id == qr.id)).scalar_one()
    assert not q.conforms_to_part_number and q.needs_review
    rec = db.execute(select(Recommendation).where(Recommendation.solicitation_line_id == ln.id)).scalar_one()
    assert rec.no_bid and "approved-source-only" in rec.no_bid_reason


def test_followups_and_no_response(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = db.get(SolicitationLine, line_ids[0])
    qr = _draft(client, db, ln)
    qr.email_to = "x@supplier.example.com"
    qr.status = QuoteRequestStatus.sent
    qr.sent_at = datetime.now(UTC) - timedelta(hours=49)
    db.commit()
    res = run_followups(db)
    db.commit()
    assert res["followups_sent"] >= 1
    db.refresh(qr)
    assert qr.followup_count == 1
    assert db.execute(select(EmailMessage).where(EmailMessage.kind == "followup")).first()
    # Due date passes -> no_response.
    ln.solicitation.return_by_date = datetime.now(UTC).date() - timedelta(days=2)
    db.commit()
    res = run_followups(db)
    db.commit()
    db.refresh(qr)
    assert qr.status == QuoteRequestStatus.no_response and res["marked_no_response"] >= 1


def test_manual_quote_and_actual_bid(client, db):
    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids, aso=False)
    qr = _draft(client, db, ln)
    r = client.post(f"/api/quote-requests/{qr.id}/quotes", json={"unit_price": 2.5, "lead_time_days": 10}, headers=csrf(client))
    assert r.status_code == 201
    r = client.post(f"/api/rfq/lines/{ln.id}/actual-bid", json={"actual_bid": 3.999}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["actual_bid"] == 4.0
    detail = client.get(f"/api/rfq/{ln.solicitation.solicitation_number}").json()
    line = detail["lines"][0]
    assert line["recommendation"]["provisional"] is False
    assert any(qr_["quotes"] for c in line["candidates"] for qr_ in c["quote_requests"])


def test_own_cage_is_never_a_supplier_candidate(client, db):
    from app.models import AwardHistory, Organization

    _, line_ids, org_id = _setup(client, db)
    ln = _line_with_sources(db, line_ids)
    org = db.get(Organization, org_id)
    org.cage_code = "9OWN1"
    db.add(AwardHistory(nsn=ln.nsn, award_date=datetime(2025, 5, 1).date(), unit_price=1.0, quantity=5,
                        contract_number="SPE-OWN-1", awardee_cage="9OWN1", source="rfq_pdf_history"))
    db.commit()
    assert "9OWN1" not in {c.cage_code for c in candidate_suppliers(db, org_id, ln)}


def test_duplicate_supplier_cage_and_reinvite(client, db):
    _setup(client, db)
    r = client.post("/api/suppliers", json={"cage_code": "1DUP1", "email": "a@dup.example.com"}, headers=csrf(client))
    assert r.status_code == 201
    r = client.post("/api/suppliers", json={"cage_code": "1dup1"}, headers=csrf(client))
    assert r.status_code == 409 and "already in your supplier directory" in r.json()["detail"]
    for _ in range(2):
        assert client.post("/api/org/invitations", json={"email": "twice@broker.example.com", "role": "viewer"},
                           headers=csrf(client)).status_code == 201
    invites = client.get("/api/org/invitations").json()
    assert [i["email"] for i in invites].count("twice@broker.example.com") == 1
