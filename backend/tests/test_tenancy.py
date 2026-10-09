"""Tenant isolation: user A must not read or modify org B's data via any endpoint or ORM path."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.tenancy import TenantViolation, bind_session_to_org
from app.models import OrgSettings, SupplierContact
from tests.conftest import csrf, make_client, signup


@pytest.fixture()
def two_orgs(app):
    a = make_client(app)
    b = make_client(app)
    a.__enter__()
    b.__enter__()
    ma = signup(a, "a@alpha.example.com", org="Alpha")
    mb = signup(b, "b@beta.example.com", org="Beta")
    yield a, b, ma, mb
    a.__exit__(None, None, None)
    b.__exit__(None, None, None)


def test_supplier_isolation(two_orgs):
    a, b, *_ = two_orgs
    r = a.post("/api/suppliers", json={"cage_code": "1AAA1", "name": "Alice", "email": "alice@sup.example.com"},
               headers=csrf(a))
    sid = r.json()["id"]
    assert [s["id"] for s in a.get("/api/suppliers").json()] == [sid]
    assert b.get("/api/suppliers").json() == []
    assert b.get(f"/api/suppliers/{sid}").status_code == 404
    assert b.patch(f"/api/suppliers/{sid}", json={"name": "Mallory"}, headers=csrf(b)).status_code == 404
    assert b.delete(f"/api/suppliers/{sid}", headers=csrf(b)).status_code == 404
    assert a.get(f"/api/suppliers/{sid}").json()["name"] == "Alice"


def test_settings_isolation(two_orgs):
    a, b, *_ = two_orgs
    assert a.put("/api/settings", json={"min_margin_pct": 0.33, "fsc_watchlist": ["5305"]},
                 headers=csrf(a)).status_code == 200
    assert b.get("/api/settings").json()["min_margin_pct"] != 0.33
    b.put("/api/settings", json={"min_margin_pct": 0.05}, headers=csrf(b))
    assert a.get("/api/settings").json()["min_margin_pct"] == 0.33
    assert a.get("/api/settings").json()["fsc_watchlist"] == ["5305"]


def test_members_isolation(two_orgs):
    a, b, *_ = two_orgs
    assert {m["email"] for m in a.get("/api/org/members").json()} == {"a@alpha.example.com"}
    assert {m["email"] for m in b.get("/api/org/members").json()} == {"b@beta.example.com"}


def test_orm_layer_filters_and_blocks_cross_org_writes(two_orgs, db):
    _, _, ma, mb = two_orgs
    org_a, org_b = ma["organization"]["id"], mb["organization"]["id"]
    db.add(SupplierContact(organization_id=org_a, cage_code="2BBB2"))
    db.commit()

    bind_session_to_org(db, org_b)
    assert db.execute(select(SupplierContact)).scalars().all() == []
    assert db.execute(select(OrgSettings)).scalars().one().organization_id == org_b

    db.add(SupplierContact(organization_id=org_a, cage_code="3CCC3"))
    with pytest.raises(TenantViolation):
        db.flush()
    db.rollback()

    # New rows without an org are stamped with the bound org.
    bind_session_to_org(db, org_b)
    row = SupplierContact(cage_code="4DDD4")
    db.add(row)
    db.flush()
    assert row.organization_id == org_b


def test_quotes_recommendations_and_rfq_actions_are_isolated(two_orgs, db):
    """Org B cannot read or modify org A's quote requests, quotes, recommendations, or call its suppliers."""
    from app.models import QuoteRequest, Recommendation, SupplierContact, SupplierQuote
    from tests.helpers import build_world

    a, b, ma, mb = two_orgs
    org_a = ma["organization"]["id"]
    _, line_ids = build_world(db, n_open=4)
    # Org A creates recommendations + drafts for the open lines.
    from app.services.recommendations import refresh_org_recommendations

    refresh_org_recommendations(db, org_a, create_quote_drafts=True)
    db.commit()
    qr = db.execute(select(QuoteRequest).where(QuoteRequest.organization_id == org_a)).scalars().first()
    assert qr is not None
    r = a.post(f"/api/quote-requests/{qr.id}/quotes", json={"unit_price": 1.5}, headers=csrf(a))
    assert r.status_code == 201
    quote_id = r.json()["id"]
    contact = SupplierContact(organization_id=org_a, cage_code=qr.cage_code, phone="+15555550000")
    db.add(contact)
    db.commit()

    # B sees none of A's quote data through any endpoint.
    for method, url, body in [
        ("patch", f"/api/quote-requests/{qr.id}", {"email_to": "evil@example.com"}),
        ("post", f"/api/quote-requests/{qr.id}/draft", None),
        ("post", f"/api/quote-requests/{qr.id}/send", None),
        ("post", f"/api/quote-requests/{qr.id}/simulate-reply", {"text": "$1.00 each"}),
        ("post", f"/api/quote-requests/{qr.id}/quotes", {"unit_price": 0.01}),
        ("patch", f"/api/supplier-quotes/{quote_id}", {"unit_price": 0.01}),
        ("post", f"/api/rfq/lines/{qr.solicitation_line_id}/call", {"supplier_contact_id": contact.id}),
    ]:
        resp = getattr(b, method)(url, json=body, headers=csrf(b)) if body is not None else getattr(b, method)(url, headers=csrf(b))
        assert resp.status_code == 404, (method, url, resp.status_code, resp.text)
    assert b.get("/api/review-queue").json() == []

    # The RFQ page itself is public data, but B only ever sees B's own recommendation and quotes.
    sn = db.get(QuoteRequest, qr.id).solicitation_line_id
    from app.models import SolicitationLine

    sol_number = db.get(SolicitationLine, sn).solicitation.solicitation_number
    detail_b = b.get(f"/api/rfq/{sol_number}").json()
    for line in detail_b["lines"]:
        for cand in line["candidates"]:
            assert all(q["id"] != qr.id for q in cand["quote_requests"])
    # B's actual bid creates B's own recommendation; A's stays untouched.
    b.post(f"/api/rfq/lines/{sn}/actual-bid", json={"actual_bid": 9.99}, headers=csrf(b))
    rec_a = db.execute(select(Recommendation).where(Recommendation.organization_id == org_a,
                                                    Recommendation.solicitation_line_id == sn)).scalar_one()
    db.refresh(rec_a)
    assert rec_a.actual_bid is None
    assert db.get(SupplierQuote, quote_id).unit_price == 1.5
    assert db.get(QuoteRequest, qr.id).email_to != "evil@example.com"
    dash_b = b.get("/api/dashboard").json()
    assert all(line["actual_bid"] in (None, 9.99) for line in dash_b["lines"])
