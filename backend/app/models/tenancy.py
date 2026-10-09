"""Organizations, users, settings and auth tokens."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import DEFAULT_FSC_WATCHLIST
from app.models.base import Base, TenantOwned
from app.models.types import StringList, UTCDateTime, utcnow


class UserRole(str, enum.Enum):
    owner = "owner"
    member = "member"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cage_code: Mapped[str | None] = mapped_column(String(5), nullable=True)
    plan: Mapped[str] = mapped_column(String(32), default="trial")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    users: Mapped[list[User]] = relationship(back_populates="organization")
    settings: Mapped[OrgSettings | None] = relationship(
        back_populates="organization", uselist=False, cascade="all, delete-orphan"
    )


class User(Base, TenantOwned):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False, length=16), default=UserRole.member)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="users")


class OrgSettings(Base, TenantOwned):
    __tablename__ = "org_settings"

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    fsc_watchlist: Mapped[list[str]] = mapped_column(StringList(), default=lambda: list(DEFAULT_FSC_WATCHLIST))
    min_margin_pct: Mapped[float] = mapped_column(Float, default=0.10)
    max_line_value: Mapped[float] = mapped_column(Float, default=25000.0)
    set_aside_eligibility: Mapped[list[str]] = mapped_column(StringList(), default=lambda: ["SB"])
    default_overhead_pct: Mapped[float] = mapped_column(Float, default=0.05)
    packaging_cost_per_line: Mapped[float] = mapped_column(Float, default=15.0)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    cost_of_capital_pct: Mapped[float] = mapped_column(Float, default=0.10)
    # Additional cost inputs (documented in DECISIONS.md)
    default_freight_per_line: Mapped[float] = mapped_column(Float, default=12.0)
    inspection_allowance_per_line: Mapped[float] = mapped_column(Float, default=0.0)
    origin_inspection_allowance_per_line: Mapped[float] = mapped_column(Float, default=75.0)
    first_article_allowance_per_line: Mapped[float] = mapped_column(Float, default=500.0)
    provisional_cost_fraction: Mapped[float] = mapped_column(Float, default=0.70)
    min_expected_profit: Mapped[float] = mapped_column(Float, default=25.0)
    # Sourcing / channels
    auto_send_rfq: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    email_sender_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email_signature: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="settings")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Invitation(Base, TenantOwned):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False, length=16), default=UserRole.member)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    invited_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
