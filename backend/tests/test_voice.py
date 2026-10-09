"""Voice module: guardrails enforced in code, mock calls end-to-end, webhook signatures."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.models import CallLog, Company, OrgSettings, PhoneType, SupplierContact, SupplierQuote
from app.services.voice.calls import call_supplier
from app.services.voice.guardrails import check_call_allowed
from app.services.voice.providers import (
    MockVoiceProvider,
    sign_elevenlabs,
    sign_twilio,
    verify_elevenlabs_signature,
    verify_twilio_signature,
)
from tests.conftest import csrf, signup
from tests.helpers import build_world

# Tue 2026-10-13 15:00 UTC = 11:00 New York, 08:00 Los Angeles.
TUE_11_ET = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
SAT_11_ET = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
TUE_19_ET = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)


@pytest.fixture()
def voice_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "voice_enabled", True)


@pytest.fixture()
def org(client, db, voice_on):
    me = signup(client, "caller@broker.example.com", org="Caller Co")
    org_id = me["organization"]["id"]
    db.get(OrgSettings, org_id).voice_enabled = True
    db.add(Company(cage_code="1ABCD", name="East Supply", state="VA"))
    db.add(Company(cage_code="2WEST", name="West Supply", state="CA"))
    db.commit()
    return org_id


def _contact(db, org_id, cage="1ABCD", ptype=PhoneType.landline, consent=False, phone="+15555550100"):
    c = SupplierContact(organization_id=org_id, cage_code=cage, phone=phone, phone_type=ptype, call_consent=consent)
    db.add(c)
    db.commit()
    return c


def test_landline_in_business_hours_allowed(db, org):
    assert check_call_allowed(db, org, _contact(db, org), TUE_11_ET).allowed


def test_mobile_without_consent_blocked_with_consent_allowed(db, org):
    c = _contact(db, org, ptype=PhoneType.mobile)
    g = check_call_allowed(db, org, c, TUE_11_ET)
    assert not g.allowed and any("consent" in r for r in g.reasons)
    c.call_consent = True
    db.commit()
    assert check_call_allowed(db, org, c, TUE_11_ET).allowed


def test_unknown_phone_type_blocked(db, org):
    assert not check_call_allowed(db, org, _contact(db, org, ptype=PhoneType.unknown), TUE_11_ET).allowed


def test_outside_business_hours_and_weekends_blocked(db, org):
    c = _contact(db, org)
    assert not check_call_allowed(db, org, c, TUE_19_ET).allowed
    assert not check_call_allowed(db, org, c, SAT_11_ET).allowed
    # 15:00 UTC is 08:00 in California: before business hours there.
    west = _contact(db, org, cage="2WEST")
    g = check_call_allowed(db, org, west, TUE_11_ET)
    assert not g.allowed and g.timezone == "America/Los_Angeles"


def test_voice_disabled_globally_or_for_org(db, org, monkeypatch):
    c = _contact(db, org)
    db.get(OrgSettings, org).voice_enabled = False
    db.commit()
    assert not check_call_allowed(db, org, c, TUE_11_ET).allowed
    db.get(OrgSettings, org).voice_enabled = True
    db.commit()
    monkeypatch.setattr(get_settings(), "voice_enabled", False)
    g = check_call_allowed(db, org, c, TUE_11_ET)
    assert not g.allowed and "VOICE_ENABLED" in g.reasons[0]


def test_one_call_per_supplier_per_day_and_mock_flow(db, org):
    build_world(db)
    from app.models import SolicitationLine

    line = db.execute(select(SolicitationLine).order_by(SolicitationLine.id.desc())).scalars().first()
    c = _contact(db, org)
    log_row = call_supplier(db, org, line, c, part_number="TEST-PN-1", provider=MockVoiceProvider(), now=TUE_11_ET)
    db.commit()
    assert log_row.disclosure_played and "AI assistant calling on behalf of" in log_row.transcript
    assert log_row.outcome in ("quoted", "declined")
    q = db.execute(select(SupplierQuote).where(SupplierQuote.quote_request_id == log_row.quote_request_id)).scalar_one()
    assert q.source == "voice"
    with pytest.raises(HTTPException) as exc:
        call_supplier(db, org, line, c, provider=MockVoiceProvider(), now=TUE_11_ET.replace(hour=17))
    assert "already called today" in str(exc.value.detail)
    # Next day is fine again.
    assert call_supplier(db, org, line, c, provider=MockVoiceProvider(), now=TUE_11_ET.replace(day=14))


def test_call_endpoint_refuses_when_blocked(client, db, org):
    build_world(db)
    from app.models import SolicitationLine

    line = db.execute(select(SolicitationLine)).scalars().first()
    c = _contact(db, org, ptype=PhoneType.mobile)
    r = client.post(f"/api/rfq/lines/{line.id}/call", json={"supplier_contact_id": c.id}, headers=csrf(client))
    assert r.status_code == 409
    assert db.execute(select(CallLog)).first() is None


def test_signatures():
    body = b'{"type":"post_call_transcription"}'
    sig = sign_elevenlabs("secret", body)
    assert verify_elevenlabs_signature("secret", body, sig)
    assert not verify_elevenlabs_signature("other", body, sig)
    assert not verify_elevenlabs_signature("secret", body + b"x", sig)
    assert not verify_elevenlabs_signature("secret", body, sign_elevenlabs("secret", body, t=1000))  # stale
    params = {"CallSid": "CA1", "CallStatus": "no-answer"}
    tw = sign_twilio("tok", "https://api.example.com/api/webhooks/twilio/status", params)
    assert verify_twilio_signature("tok", "https://api.example.com/api/webhooks/twilio/status", params, tw)
    assert not verify_twilio_signature("tok", "https://evil.example.com/x", params, tw)


def test_webhooks_reject_unsigned(client):
    body = json.dumps({"type": "post_call_transcription", "data": {"conversation_id": "c1"}}).encode()
    assert client.post("/api/webhooks/elevenlabs", content=body).status_code == 401
    assert client.post("/api/webhooks/elevenlabs", content=body,
                       headers={"elevenlabs-signature": sign_elevenlabs("test-eleven-secret", body)}).status_code == 200
    assert client.post("/api/webhooks/twilio/status", data={"CallSid": "x"}).status_code == 401
