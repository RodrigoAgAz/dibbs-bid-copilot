"""The public demo's simulated quotes: fictional suppliers only, demo orgs only, real lines only."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models import Organization, QuoteRequest, Recommendation, Solicitation, SupplierContact, SupplierQuote
from app.services.demo_refresh import DEMO_SUPPLIERS, refresh_demo_orgs
from tests.conftest import signup
from tests.helpers import build_world


def test_demo_refresh_quotes_real_lines_with_fictional_suppliers(client, db):
    build_world(db, n_nsns=60, n_open=16)
    # Make most open lines look like real DIBBS solicitations (the refresher skips SPEZZ demo numbers); a few
    # SPEZZ practice lines with seeded quotes must not count toward the demo's quoted-line target.
    open_sols = db.execute(select(Solicitation).where(Solicitation.return_by_date >= date.today())).scalars().all()
    for i, sol in enumerate(open_sols[2:]):
        sol.solicitation_number = f"SPE7M026T{9000 + i}"
    db.commit()
    demo = signup(client, "demo-owner@atlas-demo.example.com", org="Atlas Federal Supply (demo)")
    client.cookies.clear()
    real = signup(client, "owner@realbroker.example.com", org="Real Broker")
    org = db.get(Organization, demo["organization"]["id"])
    org.plan = "demo"
    db.commit()
    from app.services.demo_refresh import TARGET_QUOTED_LINES

    practice = [x for x in open_sols[:2]]
    for sol in practice:  # seeded quotes on practice lines, as on the live demo org
        for ln in sol.lines:
            for k in range(TARGET_QUOTED_LINES):
                db.add(QuoteRequest(organization_id=org.id, solicitation_line_id=ln.id, cage_code=f"P{k:04d}",
                                    channel="email", status="sent", reply_token=f"practice-{sol.id}-{ln.id}-{k}"))
    # Leftover unsent drafts on real lines (from before drafts were on demand) don't count either.
    for sol in open_sols[2:]:
        for ln in sol.lines:
            for k in range(TARGET_QUOTED_LINES):
                db.add(QuoteRequest(organization_id=org.id, solicitation_line_id=ln.id, cage_code=f"D{k:04d}",
                                    channel="email", status="draft", reply_token=f"practice-draft-{ln.id}-{k}"))
    db.commit()

    out = refresh_demo_orgs(db)
    assert len(out) == 1 and out[0]["lines"] > 0 and out[0]["replies"] > 0
    qrs = [q for q in db.execute(select(QuoteRequest).where(QuoteRequest.organization_id == org.id)).scalars()
           if not q.reply_token.startswith("practice-")]
    fictional = {c for c, _, _ in DEMO_SUPPLIERS}
    assert qrs and {q.cage_code for q in qrs} <= fictional
    assert all(q.email_to.endswith(".example.com") for q in qrs)
    assert db.query(SupplierQuote).filter(SupplierQuote.organization_id == org.id).count() > 0
    contacts = db.execute(select(SupplierContact).where(SupplierContact.organization_id == org.id)).scalars().all()
    assert all("(demo)" in (c.company_name or "") for c in contacts if c.cage_code in fictional)
    # Some lines now have a firm (quoted) recommendation.
    firm = db.execute(select(Recommendation).where(Recommendation.organization_id == org.id,
                                                  Recommendation.provisional.is_(False))).scalars().all()
    assert firm
    # The real customer is untouched.
    assert db.query(QuoteRequest).filter(QuoteRequest.organization_id == real["organization"]["id"]).count() == 0
    assert db.query(SupplierContact).filter(SupplierContact.organization_id == real["organization"]["id"]).count() == 0
