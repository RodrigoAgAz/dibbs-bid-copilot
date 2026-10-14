"""Operations: award-check backoff, job queues, data health, read-only viewers, demo login, account hardening."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.config import get_settings
from app.models import (
    AwardHistory,
    IngestCheckpoint,
    IngestionRun,
    Organization,
    OrgSettings,
    Recommendation,
    Solicitation,
    SolicitationLine,
    User,
    UserRole,
)
from app.services.accounts import DEMO_VIEWER_EMAIL, DEMO_VIEWER_ORG
from tests.conftest import csrf, signup

TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)


@pytest.fixture()
def settings(monkeypatch):
    """The cached Settings instance; attributes set through monkeypatch are restored after the test."""
    s = get_settings()

    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)
        return s

    return set_


# ----------------------------------------------------------------------------- award-check backoff
def _sol(db, org_id, n, closed_days_ago, *, checked_days_ago=None, status=None, bid=False):
    sol = Solicitation(solicitation_number=f"SPE7M026T{n:04d}", return_by_date=TODAY - timedelta(days=closed_days_ago),
                       status=status,
                       award_checked_at=NOW - timedelta(days=checked_days_ago) if checked_days_ago is not None else None)
    db.add(sol)
    db.flush()
    nsn = f"5305000{n:06d}"
    line = SolicitationLine(solicitation_id=sol.id, line_no=1, nsn=nsn, fsc="5305", niin=nsn[4:], quantity=10)
    db.add(line)
    db.flush()
    db.add(Recommendation(organization_id=org_id, solicitation_line_id=line.id, actual_bid=1.0 if bid else None))
    db.flush()
    return sol


def test_award_checks_skip_open_awarded_cancelled_and_recently_checked(db):
    from app.pipelines.awards import solicitations_due_for_award_check

    org = Organization(name="O")
    db.add(org)
    db.flush()
    due_new = _sol(db, org.id, 1, closed_days_ago=3)
    _sol(db, org.id, 2, closed_days_ago=-2)  # still open
    _sol(db, org.id, 3, closed_days_ago=5, checked_days_ago=1)  # checked yesterday, 2-day interval
    due_rechk = _sol(db, org.id, 4, closed_days_ago=5, checked_days_ago=3)
    _sol(db, org.id, 5, closed_days_ago=30, checked_days_ago=4)  # 5-day interval
    _sol(db, org.id, 6, closed_days_ago=8, status="Cancelled")
    awarded = _sol(db, org.id, 7, closed_days_ago=8)
    db.add(AwardHistory(nsn="5305000000007", award_date=TODAY, unit_price=1.0, quantity=10, contract_number="X1",
                        solicitation_number=awarded.solicitation_number, source="record_page"))
    _sol(db, org.id, 8, closed_days_ago=90)  # past the tracking window
    acted = _sol(db, org.id, 9, closed_days_ago=20, bid=True)
    db.commit()

    due = solicitations_due_for_award_check(db, TODAY, NOW, limit=100)
    assert [s.solicitation_number for s in due] == [
        acted.solicitation_number,  # lines a broker bid on come first
        due_new.solicitation_number,  # then the most recently closed
        due_rechk.solicitation_number,
    ]
    assert len(solicitations_due_for_award_check(db, TODAY, NOW, limit=1)) == 1


def test_award_labeling_stamps_checked_solicitations(db, monkeypatch):
    from app.pipelines import awards

    org = Organization(name="O")
    db.add(org)
    db.flush()
    sol = _sol(db, org.id, 1, closed_days_ago=3)
    db.commit()
    monkeypatch.setattr(awards, "today_et", lambda: TODAY)
    res = awards.run_award_labeling(db, mode="fixture", max_post_dates=0)
    assert res["record_pages_due"] == 1
    db.refresh(sol)
    assert sol.award_checked_at is not None
    again = awards.run_award_labeling(db, mode="fixture", max_post_dates=0)
    assert again["record_pages_due"] == 0  # backoff: not re-checked the same day


# ----------------------------------------------------------------------------- job queues
def test_bulk_jobs_have_their_own_queue_and_worker_pools_parse():
    from app.jobs import tasks
    from app.jobs.queue import BULK_QUEUE, QUEUE_NAME, queue_for
    from app.jobs.worker import parse_pools

    for f in (tasks.award_labeling_job, tasks.retrain_job, tasks.nightly_backfill_job, tasks.initial_live_load_job):
        assert queue_for(f) == BULK_QUEUE
    for f in (tasks.daily_ingest_job, tasks.followups_job, tasks.ops_check_job, tasks.send_quote_request_job):
        assert queue_for(f) == QUEUE_NAME
    assert parse_pools("default;bulk") == [["default"], ["bulk"]]
    assert parse_pools(" default , bulk ") == [["default", "bulk"]]
    assert parse_pools("") == [["default", "bulk"]]


def test_scheduler_registers_every_job():
    from app.jobs.scheduler import build_scheduler

    ids = {j.id for j in build_scheduler().get_jobs()}
    assert ids == {"daily_ingest", "nightly_backfill", "award_labeling", "followups", "ops_check", "retrain",
                   "daily_digest"}


# ----------------------------------------------------------------------------- data health
def test_data_health_fixture_mode_only_requires_a_model(client):
    r = client.get("/api/health/data")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded" and body["model"] is None
    assert [c["name"] for c in body["checks"]] == ["A pricing model is active"]


def test_data_health_live_mode_flags_stale_pipelines(db, settings):
    from app.services.ops import data_health

    settings(connector_mode="live")
    h = data_health(db, NOW)
    assert h["status"] == "degraded"
    assert {c["name"] for c in h["checks"] if not c["ok"]} >= {
        "RFQ ingest succeeded in the last 14 h", "Award labeling succeeded in the last 30 h",
        "Newest DIBBS RFQ file is at most 4 days old",
    }
    db.add_all([
        IngestionRun(source="dibbs_rfq:live", status="success", started_at=NOW - timedelta(hours=1),
                     finished_at=NOW - timedelta(minutes=50)),
        IngestionRun(source="dibbs_awards:live", status="partial", started_at=NOW - timedelta(hours=9),
                     finished_at=NOW - timedelta(hours=8)),
        IngestCheckpoint(source="rfq_index", key=(date.today() - timedelta(days=1)).isoformat(), status="done"),
    ])
    db.commit()
    h = data_health(db, NOW)
    failing = {c["name"] for c in h["checks"] if not c["ok"]}
    assert "RFQ ingest succeeded in the last 14 h" not in failing
    assert "Award labeling succeeded in the last 30 h" not in failing
    assert "Newest DIBBS RFQ file is at most 4 days old" not in failing
    assert h["runs"]["dibbs_rfq"]["last_status"] == "success"


def test_ops_alert_email_is_throttled(db, settings):
    from app.models import EmailMessage
    from app.services.ops import run_ops_check

    settings(connector_mode="live", ops_alert_email="ops@broker.example.com")
    assert run_ops_check(db, NOW)["alerted"] is True
    db.commit()
    assert run_ops_check(db, NOW + timedelta(hours=1))["alerted"] is False
    db.commit()
    assert run_ops_check(db, NOW + timedelta(hours=13))["alerted"] is True
    sent = db.query(EmailMessage).filter(EmailMessage.kind == "ops_alert").count()
    assert sent == 2


# ----------------------------------------------------------------------------- viewers and the public demo
def _demo_org(db):
    org = Organization(name=DEMO_VIEWER_ORG, cage_code="9ZDM1", plan="demo")
    db.add(org)
    db.flush()
    db.add(OrgSettings(organization_id=org.id))
    db.commit()
    return org


def test_public_demo_is_off_by_default(client, db):
    _demo_org(db)
    assert client.get("/api/auth/options").json() == {"public_demo": False}
    assert client.post("/api/auth/demo").status_code == 404


def test_public_demo_signs_in_a_read_only_viewer(client, db, settings):
    settings(public_demo=True)
    org = _demo_org(db)
    assert client.get("/api/auth/options").json() == {"public_demo": True}
    r = client.post("/api/auth/demo")
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["user"]["email"] == DEMO_VIEWER_EMAIL and me["user"]["role"] == "viewer"
    assert me["organization"]["id"] == org.id
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/settings").status_code == 200
    r = client.put("/api/settings", json={"min_margin_pct": 0.5}, headers=csrf(client))
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert client.post("/api/suppliers", json={"cage_code": "1ABC2"}, headers=csrf(client)).status_code == 403
    assert client.post("/api/org/invitations", json={"email": "x@y.example.com"}, headers=csrf(client)).status_code == 403
    assert client.post("/api/auth/logout", headers=csrf(client)).status_code == 200


def test_public_demo_without_a_seeded_demo_org_is_unavailable(client, settings):
    settings(public_demo=True)
    assert client.post("/api/auth/demo").status_code == 503


def test_owner_can_invite_a_viewer(client):
    signup(client, "owner@broker.example.com", org="Broker")
    r = client.post("/api/org/invitations", json={"email": "acct@broker.example.com", "role": "viewer"},
                    headers=csrf(client))
    assert r.status_code == 201 and r.json()["role"] == "viewer"


# ----------------------------------------------------------------------------- account hardening
def test_published_demo_passwords_are_locked(client, db, settings):
    from app.core.security import hash_password
    from app.seed.demo import DEMO_ORGS
    from app.services.accounts import secure_accounts

    org = _demo_org(db)
    published = DEMO_ORGS[0]
    db.add(User(organization_id=org.id, email=published["email"], password_hash=hash_password(published["password"]),
                role=UserRole.owner))
    db.commit()
    login = {"email": published["email"], "password": published["password"]}
    assert client.post("/api/auth/login", json=login).status_code == 200

    settings(demo_password_login=True)
    assert "demo_passwords_locked" not in secure_accounts(db)
    settings(demo_password_login=False)
    assert secure_accounts(db)["demo_passwords_locked"] == 1
    db.commit()
    assert client.post("/api/auth/login", json=login).status_code == 401
    assert client.post("/api/auth/refresh", headers=csrf(client)).status_code == 401  # existing session revoked
    assert secure_accounts(db)["demo_passwords_locked"] == 0  # idempotent


def test_bootstrap_admin_is_created_and_password_follows_the_variable(client, db, settings):
    from app.services.accounts import secure_accounts

    settings(bootstrap_admin_email="Ops@Broker.example.com", bootstrap_admin_password="first-long-password")
    assert secure_accounts(db)["bootstrap_admin"] is True
    db.commit()
    r = client.post("/api/auth/login", json={"email": "ops@broker.example.com", "password": "first-long-password"})
    assert r.status_code == 200 and r.json()["user"]["is_superadmin"] is True
    settings(bootstrap_admin_password="second-long-password")
    secure_accounts(db)
    db.commit()
    assert client.post("/api/auth/login", json={"email": "ops@broker.example.com",
                                                "password": "first-long-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ops@broker.example.com",
                                                "password": "second-long-password"}).status_code == 200
    settings(bootstrap_admin_password="short")
    assert "bootstrap_admin" not in secure_accounts(db)


def test_rules_change_queues_one_full_refresh(client, db, monkeypatch):
    from app import cli
    from app.jobs import queue, tasks
    from app.services.recommendations import ENGINE_VERSION
    from tests.helpers import build_world

    calls = []
    monkeypatch.setattr(queue, "enqueue_unique", lambda func, *a, **k: calls.append((func.__name__, a)) or "job-1")
    assert tasks  # the check lives in app.jobs.tasks (bootstrap + hourly ops check)
    cli._maybe_queue_engine_refresh()
    assert calls == []  # no recommendations yet: nothing to recompute
    build_world(db, n_open=6)
    signup(client, "engine@broker.example.com", org="Engine Org")
    cli._maybe_queue_engine_refresh()
    assert calls == [("refresh_all_recommendations_job", (ENGINE_VERSION,))]
    out = tasks.refresh_all_recommendations_job(ENGINE_VERSION)
    assert sum(out.values()) > 0
    calls.clear()
    cli._maybe_queue_engine_refresh()
    assert calls == []  # marker now matches the engine version


def test_pending_job_check_ignores_jobs_abandoned_by_a_killed_worker(monkeypatch):
    import time

    import fakeredis
    from rq import Queue
    from rq.registry import StartedJobRegistry

    from app.jobs import queue as jq
    from app.jobs.tasks import ops_check_job

    conn = fakeredis.FakeStrictRedis()
    q = Queue(jq.QUEUE_NAME, connection=conn)
    monkeypatch.setattr(jq, "get_queue", lambda name=jq.QUEUE_NAME: q)
    assert jq.pending_job_id(ops_check_job) is None
    job = q.enqueue(ops_check_job)
    assert jq.pending_job_id(ops_check_job) == job.id  # queued
    # Picked up by a worker that was then killed: still in the started registry, heartbeat goes stale.
    q.remove(job.id)
    started = datetime.now(UTC) - timedelta(minutes=10)
    job.started_at, job.last_heartbeat = started, started
    job.save()
    # RQ 2.x tracks started jobs as "<job id>:<execution id>" members of the registry sorted set.
    conn.zadd(StartedJobRegistry(queue=q).key, {f"{job.id}:exec-1": time.time() + 7200})
    assert jq.pending_job_id(ops_check_job) is None
    # A job with a fresh heartbeat is really running.
    job.last_heartbeat = datetime.now(UTC)
    job.save()
    assert jq.pending_job_id(ops_check_job) == job.id
