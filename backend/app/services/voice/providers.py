"""Voice providers (feature flag VOICE_ENABLED, default off).

* MockVoiceProvider (default): simulates a supplier conversation with a deterministic transcript.
* TwilioElevenLabsVoiceProvider: ElevenLabs Conversational AI agent placing the call over a Twilio
  number (ElevenLabs' Twilio outbound-call API). The transcript arrives later via the ElevenLabs
  post-call webhook (POST /api/webhooks/elevenlabs). Whisper (OpenAI) is used only as a post-call
  transcription fallback when the agent returns no transcript.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# The agent's system prompt (configured on the ElevenLabs agent; dynamic variables are filled per call).
AGENT_SYSTEM_PROMPT = """You are an AI assistant calling a parts supplier on behalf of {{broker_name}}, a small
federal contractor. Follow these rules exactly:
1. Start by saying: "Hi, this is an AI assistant calling on behalf of {{broker_name}}." Then say: "This call may
   be recorded. Is it okay to continue?" If they say no, thank them and end the call.
2. Ask for a price and lead time for part number {{part_number}} (NSN {{nsn}}), quantity {{quantity}} {{unit}}.
   Also ask for minimum order quantity and how long the quote is valid, if they offer it.
3. Offer to send the full details by email to whoever handles quotes, and note the email address if given.
4. Never commit to an order, never negotiate terms you were not given, never share other suppliers' prices.
5. If they ask to stop, are busy, or ask you to end the call, end it politely right away.
Keep it brief and courteous."""

DISCLOSURE = "Hi, this is an AI assistant calling on behalf of {broker}. This call may be recorded. Is it okay to continue?"


@dataclass
class CallRequest:
    quote_request_id: int
    to_number: str
    broker_name: str
    supplier_name: str
    part_number: str
    nsn: str
    quantity: float
    unit: str
    price_hint: float | None = None  # used only by the mock to produce a plausible quote
    seed: str = ""


@dataclass
class CallResult:
    provider: str
    provider_call_id: str | None
    status: str  # completed | queued | failed
    disclosure_played: bool = False
    transcript: str | None = None
    recording_url: str | None = None
    outcome: str | None = None  # quoted | declined | no_answer | queued | failed
    error: str | None = None
    extra: dict = field(default_factory=dict)


class VoiceProvider:
    name = "base"

    def place_call(self, req: CallRequest) -> CallResult:
        raise NotImplementedError


class MockVoiceProvider(VoiceProvider):
    name = "mock"

    def place_call(self, req: CallRequest) -> CallResult:
        h = int(hashlib.sha256(f"{req.seed}|{req.nsn}|{req.to_number}".encode()).hexdigest(), 16)
        disclosure = DISCLOSURE.format(broker=req.broker_name)
        lines = [f"AGENT: {disclosure}", "SUPPLIER: Sure, go ahead."]
        lines.append(
            f"AGENT: Thanks. I'm looking for a price and lead time on part number {req.part_number}, "
            f"NSN {req.nsn}, quantity {req.quantity:g} {req.unit}."
        )
        if h % 5 == 0:
            lines += [
                "SUPPLIER: Sorry, we don't carry that part anymore. We can't quote it.",
                "AGENT: Understood, thank you for your time. Goodbye.",
            ]
            outcome = "declined"
        else:
            base = req.price_hint or 10.0
            price = round(base * (0.55 + (h % 30) / 100), 2)
            weeks = 2 + h % 6
            lines += [
                f"SUPPLIER: Let me check... P/N {req.part_number}, we can do ${price:.2f} each, "
                f"lead time about {weeks} weeks ARO. Quote valid for 30 days. Minimum order is {1 + h % 10}.",
                "AGENT: Great, thank you. Can I send the full details by email to confirm?",
                "SUPPLIER: Yes, send it to our quotes inbox.",
                "AGENT: Will do. To be clear, this is a request for a quote, not an order. Thanks, goodbye.",
            ]
            outcome = "quoted"
        return CallResult(
            provider=self.name,
            provider_call_id=f"mock-{h % 10**10}",
            status="completed",
            disclosure_played=True,
            transcript="\n".join(lines),
            outcome=outcome,
        )


class TwilioElevenLabsVoiceProvider(VoiceProvider):
    name = "twilio_elevenlabs"
    OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"

    def __init__(self, client: httpx.Client | None = None) -> None:
        s = get_settings()
        missing = [k for k in ("elevenlabs_api_key", "elevenlabs_agent_id", "elevenlabs_phone_number_id") if not getattr(s, k)]
        if missing:
            raise RuntimeError(f"voice provider not configured: missing {', '.join(missing)}")
        self.s = s
        self.client = client or httpx.Client(timeout=30)

    def place_call(self, req: CallRequest) -> CallResult:
        body = {
            "agent_id": self.s.elevenlabs_agent_id,
            "agent_phone_number_id": self.s.elevenlabs_phone_number_id,
            "to_number": req.to_number,
            "conversation_initiation_client_data": {
                "dynamic_variables": {
                    "broker_name": req.broker_name,
                    "supplier_name": req.supplier_name,
                    "part_number": req.part_number,
                    "nsn": req.nsn,
                    "quantity": f"{req.quantity:g}",
                    "unit": req.unit,
                    "quote_request_id": str(req.quote_request_id),
                }
            },
        }
        try:
            r = self.client.post(self.OUTBOUND_URL, json=body, headers={"xi-api-key": self.s.elevenlabs_api_key})
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            return CallResult(self.name, None, "failed", outcome="failed", error=str(exc)[:500])
        return CallResult(
            provider=self.name,
            provider_call_id=data.get("conversation_id") or data.get("callSid"),
            status="queued",
            disclosure_played=False,  # confirmed from the transcript when the post-call webhook arrives
            outcome="queued",
            extra={"call_sid": data.get("callSid")},
        )


def get_voice_provider() -> VoiceProvider:
    s = get_settings()
    if s.voice_provider == "twilio_elevenlabs":
        return TwilioElevenLabsVoiceProvider()
    return MockVoiceProvider()


# ----------------------------------------------------------------------------- webhook signatures
def verify_twilio_signature(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """X-Twilio-Signature: base64(HMAC-SHA1(auth_token, url + concatenated sorted key+value pairs))."""
    if not auth_token or not signature:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def sign_twilio(auth_token: str, url: str, params: dict[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def verify_elevenlabs_signature(secret: str, raw_body: bytes, header: str, tolerance_s: int = 1800) -> bool:
    """ElevenLabs-Signature: 't=<unix>,v0=<hex HMAC-SHA256(secret, "<t>.<body>")>'."""
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, v0 = parts.get("t"), parts.get("v0")
    if not t or not v0 or not t.isdigit():
        return False
    if abs(time.time() - int(t)) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v0)


def sign_elevenlabs(secret: str, raw_body: bytes, t: int | None = None) -> str:
    t = t or int(time.time())
    return f"t={t},v0=" + hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()


def transcribe_with_whisper(recording_url: str) -> str | None:
    """Post-call transcription fallback (only when the agent returned no transcript)."""
    key = get_settings().openai_api_key
    if not key or not recording_url:
        return None
    try:
        with httpx.Client(timeout=120) as c:
            audio = c.get(recording_url).content
            r = c.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("call.mp3", audio, "audio/mpeg")},
                data={"model": "whisper-1"},
            )
            r.raise_for_status()
            return r.json().get("text")
    except Exception:
        log.exception("whisper transcription failed")
        return None

