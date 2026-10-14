"""Daily opportunity digest: content, opt-out, idempotency, who never gets one."""

from __future__ import annotations

from sqlalchemy import select

from app.models import EmailMessage, Organization
from app.services.digest import build_digest, render_digest, send_daily_digests
from app.services.email.providers import ConsoleEmailProvider
from tests.conftest import csrf, signup
from tests.helpers import build_world


def _digests(db) -> list[EmailMessage]:
    return list(db.execute(select(EmailMessage).where(EmailMessage.kind == "digest")).scalars())


def test_digest_lists_new_bid_worthy_lines_with_links(client, db):
    from app.core.timeutil import today_et

    build_world(db, n_open=8)
    me = signup(client, "digest@broker.example.com", org="Digest Org")
    d = build_digest(db, me["organization"]["id"])
    assert d.new_bid_lines > 0 and d.top and not d.empty
    assert [x.expected_profit for x in d.top] == sorted([x.expected_profit for x in d.top], reverse=True)
    subject, body = render_digest(d, today_et())
    assert "new DIBBS lines worth bidding" in subject
    assert f"/rfq/{d.top[0].solicitation_number}" in body and "/settings" in body
    assert "submit your bids on DIBBS" in body


def test_digest_is_sent_once_per_day_and_respects_opt_out(client, db):
    build_world(db, n_open=8)
    signup(client, "once@broker.example.com", org="Once Org")
    provider = ConsoleEmailProvider()
    assert send_daily_digests(db, provider)["sent"] == 1
    assert send_daily_digests(db, provider)["sent"] == 0  # already sent today
    assert [m.to_addr for m in _digests(db)] == ["once@broker.example.com"]

    r = client.put("/api/auth/me/preferences", json={"email_digest": False}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["user"]["email_digest"] is False
    db.query(EmailMessage).delete()
    from app.models import IngestCheckpoint

    db.query(IngestCheckpoint).filter(IngestCheckpoint.source == "digest").delete()
    db.commit()
    assert send_daily_digests(db, provider)["sent"] == 0


def test_no_digest_for_demo_orgs_or_without_an_email_provider(client, db):
    build_world(db, n_open=8)
    me = signup(client, "demo@broker.example.com", org="Demo-ish Org")
    org = db.get(Organization, me["organization"]["id"])
    # No explicit provider and EMAIL_PROVIDER=console: skipped entirely.
    assert send_daily_digests(db)["skipped"]
    org.plan = "demo"
    db.commit()
    assert send_daily_digests(db, ConsoleEmailProvider())["sent"] == 0
    assert _digests(db) == []
