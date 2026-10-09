"""Tenant-owned supplier sourcing data: contacts, quote requests, quotes, emails, calls."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Boolean, Date, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantOwned
from app.models.types import JSONType, StringList, UTCDateTime, utcnow


class PhoneType(str, enum.Enum):
    landline = "landline"
    mobile = "mobile"
    unknown = "unknown"


class Channel(str, enum.Enum):
    email = "email"
    voice = "voice"


class QuoteRequestStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    replied = "replied"
    parsed = "parsed"
    no_response = "no_response"
    declined = "declined"
    failed = "failed"


def _enum(e: type[enum.Enum], length: int = 16) -> Enum:
    return Enum(e, native_enum=False, length=length)


class SupplierContact(Base, TenantOwned):
    __tablename__ = "supplier_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    cage_code: Mapped[str] = mapped_column(String(5), index=True)
    company_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_type: Mapped[PhoneType] = mapped_column(_enum(PhoneType), default=PhoneType.unknown)
    call_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fsc_tags: Mapped[list[str]] = mapped_column(StringList(), default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class QuoteRequest(Base, TenantOwned):
    __tablename__ = "quote_requests"
    __table_args__ = (UniqueConstraint("organization_id", "solicitation_line_id", "cage_code", "channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    solicitation_line_id: Mapped[int] = mapped_column(
        ForeignKey("solicitation_lines.id", ondelete="CASCADE"), index=True
    )
    cage_code: Mapped[str] = mapped_column(String(5), index=True)
    supplier_contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_contacts.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[Channel] = mapped_column(_enum(Channel), default=Channel.email)
    status: Mapped[QuoteRequestStatus] = mapped_column(_enum(QuoteRequestStatus), default=QuoteRequestStatus.draft)
    candidate_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # approved_source|historical|saved
    part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reply_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    followup_count: Mapped[int] = mapped_column(Integer, default=0)
    last_followup_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    quotes: Mapped[list[SupplierQuote]] = relationship(back_populates="quote_request", cascade="all, delete-orphan")


class SupplierQuote(Base, TenantOwned):
    __tablename__ = "supplier_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    quote_request_id: Mapped[int] = mapped_column(ForeignKey("quote_requests.id", ondelete="CASCADE"), index=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    quoted_part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    min_order_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    freight_terms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    freight_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    certs_offered: Mapped[list[str]] = mapped_column(StringList(), default=list)
    conforms_to_part_number: Mapped[bool] = mapped_column(Boolean, default=True)
    declined: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="email")  # email | voice | manual
    parsed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    quote_request: Mapped[QuoteRequest] = relationship(back_populates="quotes")


class EmailMessage(Base, TenantOwned):
    """Every outbound and inbound email (the console provider's 'outbox' too)."""

    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    quote_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    direction: Mapped[str] = mapped_column(String(8))  # outbound | inbound
    kind: Mapped[str] = mapped_column(String(32), default="rfq")  # rfq | followup | reply | system
    provider: Mapped[str] = mapped_column(String(32), default="console")
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    from_addr: Mapped[str] = mapped_column(String(320))
    to_addr: Mapped[str] = mapped_column(String(320))
    reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CallLog(Base, TenantOwned):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    quote_request_id: Mapped[int] = mapped_column(ForeignKey("quote_requests.id", ondelete="CASCADE"), index=True)
    cage_code: Mapped[str] = mapped_column(String(5), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    provider_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    to_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    disclosure_played: Mapped[bool] = mapped_column(Boolean, default=False)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)  # quoted|declined|no_answer|blocked|...
