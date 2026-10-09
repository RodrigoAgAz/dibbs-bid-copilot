"""Email providers.

* ConsoleEmailProvider (default): logs and stores every message in `email_messages`; nothing leaves the box.
* ResendEmailProvider: sends through the Resend HTTP API, and also stores the message.

Inbound replies arrive at POST /api/webhooks/inbound-email (see app/api/routes/webhooks.py).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EmailMessage

log = logging.getLogger(__name__)


@dataclass
class OutboundEmail:
    to: str
    subject: str
    body: str
    reply_to: str | None = None
    from_addr: str | None = None
    organization_id: int | None = None
    quote_request_id: int | None = None
    kind: str = "rfq"
    attachments: list[dict] = field(default_factory=list)


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None
    message_id: int | None = None  # email_messages.id


class EmailProvider:
    name = "base"

    def send(self, db: Session, email: OutboundEmail) -> SendResult:
        raise NotImplementedError

    def _record(self, db: Session, email: OutboundEmail, provider_message_id: str | None) -> EmailMessage:
        s = get_settings()
        msg = EmailMessage(
            organization_id=email.organization_id,
            quote_request_id=email.quote_request_id,
            direction="outbound",
            kind=email.kind,
            provider=self.name,
            provider_message_id=provider_message_id,
            from_addr=email.from_addr or s.email_from,
            to_addr=email.to,
            reply_to=email.reply_to,
            subject=email.subject,
            body=email.body,
            attachments=email.attachments or None,
        )
        db.add(msg)
        db.flush()
        return msg


class ConsoleEmailProvider(EmailProvider):
    name = "console"

    def send(self, db: Session, email: OutboundEmail) -> SendResult:
        mid = f"console-{uuid.uuid4().hex[:12]}"
        msg = self._record(db, email, mid)
        log.info("[console email] to=%s subject=%r reply_to=%s\n%s", email.to, email.subject, email.reply_to, email.body)
        return SendResult(ok=True, provider_message_id=mid, message_id=msg.id)


class ResendEmailProvider(EmailProvider):
    name = "resend"
    API = "https://api.resend.com/emails"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=20)

    def send(self, db: Session, email: OutboundEmail) -> SendResult:
        s = get_settings()
        payload: dict = {
            "from": email.from_addr or s.email_from,
            "to": [email.to],
            "subject": email.subject,
            "text": email.body,
        }
        if email.reply_to:
            payload["reply_to"] = email.reply_to
        try:
            r = self.client.post(self.API, json=payload, headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
            mid = r.json().get("id")
        except Exception as exc:  # network or API error
            log.warning("Resend send failed: %s", exc)
            return SendResult(ok=False, error=str(exc)[:500])
        msg = self._record(db, email, mid)
        return SendResult(ok=True, provider_message_id=mid, message_id=msg.id)


_override: EmailProvider | None = None


def set_email_provider(provider: EmailProvider | None) -> None:
    global _override
    _override = provider


def get_email_provider() -> EmailProvider:
    if _override is not None:
        return _override
    s = get_settings()
    if s.email_provider == "resend" and s.resend_api_key:
        return ResendEmailProvider(s.resend_api_key)
    return ConsoleEmailProvider()


def reply_address(token: str) -> str:
    return f"quotes+{token}@{get_settings().email_reply_domain}"
