"""The public demo's simulated quotes: fictional suppliers only, demo orgs only, real lines only."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models import (
    EmailMessage,
    Organization,
    QuoteRequest,
    Recommendation,
    Solicitation,
    SolicitationLine,
    SupplierContact,
    SupplierQuote,
)
from app.services.demo_refresh import DEMO_SUPPLIERS, refresh_demo_orgs, tidy_demo_org
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
    assert not db.query(SupplierQuote).filter(SupplierQuote.quoted_part_number == "per").count()
    # Simulated threads read in order: RFQ email at the request's send time, the reply later (not the same instant).
    for q in qrs:
        if q.sent_at is None:
            continue
        msgs = db.execute(select(EmailMessage).where(EmailMessage.quote_request_id == q.id)).scalars().all()
        out_ = [m for m in msgs if m.direction == "outbound"]
        assert out_ and all(abs((m.created_at - q.sent_at).total_seconds()) < 2 for m in out_)
        assert all(m.created_at > q.sent_at for m in msgs if m.direction == "inbound")
    contacts = db.execute(select(SupplierContact).where(SupplierContact.organization_id == org.id)).scalars().all()
    assert all("(demo)" in (c.company_name or "") for c in contacts if c.cage_code in fictional)
    # Some lines now have a firm (quoted) recommendation.
    firm = db.execute(select(Recommendation).where(Recommendation.organization_id == org.id,
                                                  Recommendation.provisional.is_(False))).scalars().all()
    assert firm
    # The real customer is untouched.
    assert db.query(QuoteRequest).filter(QuoteRequest.organization_id == real["organization"]["id"]).count() == 0
    assert db.query(SupplierContact).filter(SupplierContact.organization_id == real["organization"]["id"]).count() == 0


def test_tidy_labels_seeded_demo_suppliers_and_fixes_old_threads(db):
    from datetime import UTC, datetime, timedelta

    org = Organization(name="Atlas Federal Supply (demo)", plan="demo")
    other = Organization(name="Real Broker")
    db.add_all([org, other])
    db.flush()
    db.add_all([
        SupplierContact(organization_id=org.id, cage_code="1AAA1", company_name="Northfield Supply Supply Co.",
                        notes="Synthetic demo supplier"),
        SupplierContact(organization_id=other.id, cage_code="1AAA1", company_name="Northfield Supply Supply Co.",
                        notes="Synthetic demo supplier"),
    ])
    sol = Solicitation(solicitation_number="SPE7M026T7777", return_by_date=date.today() + timedelta(days=5))
    db.add(sol)
    db.flush()
    line = SolicitationLine(solicitation_id=sol.id, line_no=1, nsn="5305000077777", fsc="5305", niin="000077777",
                            quantity=10)
    db.add(line)
    db.flush()
    sent = datetime.now(UTC) - timedelta(hours=30)
    qr = QuoteRequest(organization_id=org.id, solicitation_line_id=line.id, cage_code="1AAA1", channel="email",
                      status="parsed", reply_token="tidy-1", sent_at=sent)
    db.add(qr)
    db.flush()
    now = datetime.now(UTC)
    for direction, kind in (("outbound", "rfq"), ("inbound", "reply")):
        db.add(EmailMessage(organization_id=org.id, quote_request_id=qr.id, direction=direction, kind=kind,
                            from_addr="a@example.com", to_addr="b@example.com", subject="s", body="b", created_at=now))
    db.add(SupplierQuote(organization_id=org.id, quote_request_id=qr.id, unit_price=5.0, quoted_part_number="per",
                         source="email"))
    db.flush()
    assert tidy_demo_org(db, org) > 0
    names = {c.organization_id: c.company_name for c in db.query(SupplierContact)}
    assert names[org.id] == "Northfield Supply Co. (demo)"
    assert names[other.id] == "Northfield Supply Supply Co."  # real customers untouched
    assert db.query(SupplierQuote).one().quoted_part_number is None
    msgs = {m.direction: m.created_at for m in db.query(EmailMessage)}
    assert abs((msgs["outbound"] - sent).total_seconds()) < 2 and sent < msgs["inbound"] < now
    assert tidy_demo_org(db, org) == 0  # idempotent
    assert tidy_demo_org(db, other) == 0


def test_switch_demo_watchlists_moves_fastener_demo_orgs_to_the_weapons_catalog(client, db):
    from app.config import DEFAULT_FSC_WATCHLIST, DEMO_MAX_LINE_VALUE, DEMO_NICHE_WATCHLIST, LEGACY_FSC_WATCHLIST
    from app.models import OrgSettings
    from app.services.demo_refresh import switch_demo_watchlists

    ids = {}
    for name in ("atlas", "bluewater", "custom", "real"):
        client.cookies.clear()
        ids[name] = signup(client, f"owner@{name}-demo.example.com", org=f"{name} (demo)")["organization"]["id"]
    for name in ("atlas", "bluewater", "custom"):
        db.get(Organization, ids[name]).plan = "demo"
    db.get(OrgSettings, ids["atlas"]).fsc_watchlist = list(LEGACY_FSC_WATCHLIST)
    db.get(OrgSettings, ids["bluewater"]).fsc_watchlist = ["5305", "5306", "5310", "5330"]
    db.get(OrgSettings, ids["custom"]).fsc_watchlist = ["5305", "6515"]  # someone changed it: leave it alone
    db.get(OrgSettings, ids["real"]).fsc_watchlist = list(LEGACY_FSC_WATCHLIST)  # a paying firm keeps its classes
    db.commit()

    out = switch_demo_watchlists(db)
    db.commit()
    assert out == {ids["atlas"]: DEFAULT_FSC_WATCHLIST, ids["bluewater"]: DEMO_NICHE_WATCHLIST}
    assert db.get(OrgSettings, ids["atlas"]).max_line_value == DEMO_MAX_LINE_VALUE
    assert db.get(OrgSettings, ids["custom"]).fsc_watchlist == ["5305", "6515"]
    assert db.get(OrgSettings, ids["real"]).fsc_watchlist == list(LEGACY_FSC_WATCHLIST)
    assert switch_demo_watchlists(db) == {}  # once only
