"""Organizations, users, settings and auth tokens."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import DEFAULT_FSC_WATCHLIST
from app.models.base import Base, TenantOwned
from app.models.types import StringList, UTCDateTime, utcnow


class UserRole(str, enum.Enum):
    owner = "owner"
    member = "member"
    viewer = "viewer"  # read-only seat (also the public demo account)


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
    # Daily opportunity digest email (app/services/digest.py); users turn it off in Settings.
    email_digest: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())

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
    packaging_cost_per_line: Mapped[float] = mapped_column(Float, default=10.0)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    cost_of_capital_pct: Mapped[float] = mapped_column(Float, default=0.10)
    # Additional cost inputs (documented in DECISIONS.md)
    default_freight_per_line: Mapped[float] = mapped_column(Float, default=10.0)
    inspection_allowance_per_line: Mapped[float] = mapped_column(Float, default=0.0)
    origin_inspection_allowance_per_line: Mapped[float] = mapped_column(Float, default=50.0)
    first_article_allowance_per_line: Mapped[float] = mapped_column(Float, default=500.0)
    provisional_cost_fraction: Mapped[float] = mapped_column(Float, default=0.70)
    min_expected_profit: Mapped[float] = mapped_column(Float, default=25.0)
    # Never recommend a bid with a lower chance of winning (keeps the optimizer out of the unreliable tail).
    min_p_win: Mapped[float] = mapped_column(Float, default=0.2, server_default="0.2")
    # Broker effort to pursue one line (research, quote requests, entering quotes), charged in the triage score.
    pursuit_cost_per_line: Mapped[float] = mapped_column(Float, default=15.0, server_default="15")
    # Days the broker needs between receiving the part and DLA's required delivery (inspection, repack, ship).
    handling_days: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    # Portfolio and risk (services/portfolio.py). None = no limit.
    working_capital_limit: Mapped[float | None] = mapped_column(Float, nullable=True)  # $ in flight across won lines
    max_open_awards: Mapped[int | None] = mapped_column(Integer, nullable=True)  # expected awards the firm can fulfil
    weekly_sourcing_hours: Mapped[float | None] = mapped_column(Float, nullable=True)  # hours a week for pursuing lines
    pursuit_hours_per_line: Mapped[float] = mapped_column(Float, default=0.33, server_default="0.33")
    max_supplier_share: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")  # of expected capital
    # Expected fulfilment losses (rejections, expediting, replacements) as a fraction of the order value, charged in
    # the bid's expected profit.
    fulfilment_loss_pct: Mapped[float] = mapped_column(Float, default=0.02, server_default="0.02")
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
