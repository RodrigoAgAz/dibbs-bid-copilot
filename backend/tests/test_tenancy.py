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
