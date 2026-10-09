"""Signed webhooks: inbound email replies, ElevenLabs post-call transcripts, Twilio call status."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import CallLog
from app.services.sourcing import process_inbound_email
from app.services.voice.providers import transcribe_with_whisper, verify_elevenlabs_signature, verify_twilio_signature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def sign_inbound(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


@router.post("/inbound-email")
async def inbound_email(request: Request, db: Session = Depends(get_db)) -> dict:
    """Parsed inbound email (JSON): {from, to, cc?, subject, text, html?, message_id?, attachments?: [
    {filename, content_type, content_base64}]}. Header X-Inbound-Signature = hex HMAC-SHA256(secret, body)."""
    raw = await request.body()
    secret = get_settings().inbound_email_secret
    sig = request.headers.get("x-inbound-signature", "")
    if not secret or not hmac.compare_digest(sign_inbound(secret, raw), sig):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON")
    result = process_inbound_email(db, payload)
    db.commit()
    return result


def _transcript_text(data: dict) -> str:
    turns = data.get("transcript") or []
    lines = []
    for t in turns:
        role = "AGENT" if (t.get("role") or "").lower() == "agent" else "SUPPLIER"
        if t.get("message"):
            lines.append(f"{role}: {t['message']}")
    return "\n".join(lines)


@router.post("/elevenlabs")
async def elevenlabs_post_call(request: Request, db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    s = get_settings()
    if not verify_elevenlabs_signature(s.elevenlabs_webhook_secret, raw, request.headers.get("elevenlabs-signature", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    event = json.loads(raw)
    data = event.get("data") or {}
    conv_id = data.get("conversation_id")
    call = db.execute(select(CallLog).where(CallLog.provider_call_id == conv_id)).scalar_one_or_none()
    if call is None:
        return {"ok": False, "reason": "unknown conversation"}
    transcript = _transcript_text(data)
    if not transcript:
        rec_url = (data.get("metadata") or {}).get("recording_url")
        transcript = transcribe_with_whisper(rec_url) or ""
        call.recording_url = rec_url
    call.ended_at = datetime.now(UTC)
    if transcript:
        from app.services.voice.calls import finish_call

        finish_call(db, call, transcript)
    else:
        call.outcome = "no_transcript"
    db.commit()
    return {"ok": True}


@router.post("/twilio/status")
async def twilio_status(request: Request, db: Session = Depends(get_db)) -> dict:
    form = dict((await request.form()).items())
    s = get_settings()
    url = s.public_api_base_url.rstrip("/") + request.url.path
    if not verify_twilio_signature(s.twilio_auth_token, url, {k: str(v) for k, v in form.items()},
                                   request.headers.get("x-twilio-signature", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    sid = form.get("CallSid")
    call = db.execute(select(CallLog).where(CallLog.provider_call_id == sid)).scalar_one_or_none()
    if call and form.get("CallStatus") in {"busy", "no-answer", "failed", "canceled"}:
        call.outcome = str(form["CallStatus"]).replace("-", "_")
        call.ended_at = datetime.now(UTC)
        db.commit()
    return {"ok": True}
