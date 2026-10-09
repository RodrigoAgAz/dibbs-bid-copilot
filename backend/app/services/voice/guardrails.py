"""Calling guardrails, enforced in code (not just in the agent prompt):

  * voice must be enabled globally (VOICE_ENABLED) and for the organization,
  * only contacts with phone_type == landline OR explicit call_consent,
  * only during the supplier's local business hours (Mon-Fri 09:00-17:00),
  * at most one call per supplier (CAGE) per organization per (supplier-local) day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CallLog, Company, OrgSettings, PhoneType, SupplierContact

BUSINESS_START = time(9, 0)
BUSINESS_END = time(17, 0)
DEFAULT_TZ = "America/New_York"

STATE_TZ = {
    **dict.fromkeys(["CT", "DE", "DC", "FL", "GA", "ME", "MD", "MA", "NH", "NJ", "NY", "NC", "OH", "PA", "RI",
                     "SC", "VT", "VA", "WV", "MI", "IN", "KY"], "America/New_York"),
    **dict.fromkeys(["AL", "AR", "IL", "IA", "LA", "MN", "MS", "MO", "OK", "WI", "TX", "KS", "NE", "SD", "ND",
                     "TN"], "America/Chicago"),
    **dict.fromkeys(["CO", "MT", "NM", "UT", "WY", "ID"], "America/Denver"),
    "AZ": "America/Phoenix",
    **dict.fromkeys(["CA", "NV", "OR", "WA"], "America/Los_Angeles"),
    "AK": "America/Anchorage",
    "HI": "Pacific/Honolulu",
}


@dataclass
class GuardrailResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    local_time: str | None = None
    timezone: str | None = None


def supplier_timezone(db: Session, contact: SupplierContact) -> ZoneInfo:
    if contact.timezone:
        try:
            return ZoneInfo(contact.timezone)
        except Exception:
            pass
    comp = db.get(Company, contact.cage_code)
    tz = STATE_TZ.get((comp.state or "").upper()) if comp else None
    return ZoneInfo(tz or DEFAULT_TZ)


def check_call_allowed(db: Session, org_id: int, contact: SupplierContact, now: datetime | None = None) -> GuardrailResult:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    reasons: list[str] = []
    if not get_settings().voice_enabled:
        reasons.append("voice calling is disabled on this server (VOICE_ENABLED=false)")
    s = db.get(OrgSettings, org_id)
    if s is None or not s.voice_enabled:
        reasons.append("voice calling is disabled for this organization")
    if not contact.phone:
        reasons.append("contact has no phone number")
    if not (contact.phone_type == PhoneType.landline or contact.call_consent):
        reasons.append("contact is not a landline and has not consented to calls")
    tz = supplier_timezone(db, contact)
    local = now.astimezone(tz)
    if local.weekday() >= 5 or not (BUSINESS_START <= local.time() < BUSINESS_END):
        reasons.append(f"outside the supplier's business hours (local time {local:%a %H:%M} {tz.key})")
    day_start_local = datetime.combine(local.date(), time(0, 0), tzinfo=tz)
    day_start_utc = day_start_local.astimezone(UTC)
    already = db.execute(
        select(CallLog.id).where(
            CallLog.organization_id == org_id,
            CallLog.cage_code == contact.cage_code,
            CallLog.started_at >= day_start_utc,
            CallLog.started_at < day_start_utc + timedelta(days=1),
            CallLog.outcome != "blocked",
        )
    ).first()
    if already:
        reasons.append("this supplier was already called today")
    return GuardrailResult(not reasons, reasons, local.strftime("%Y-%m-%d %H:%M"), tz.key)
