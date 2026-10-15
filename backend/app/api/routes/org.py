"""Organization: members and invitations, and per-org settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import get_tenant, require_owner
from app.config import get_settings
from app.core.security import hash_token, new_opaque_token
from app.core.tenancy import SKIP_TENANT_FILTER, TenantContext
from app.models import Invitation, OrgSettings, User, UserRole
from app.schemas.auth import InvitationOut, InviteCreatedOut, InviteIn, OkOut, UserOut
from app.services.email.providers import OutboundEmail, get_email_provider

router = APIRouter(prefix="/api", tags=["organization"])

FSC_PATTERN = r"^\d{4}$"


@router.get("/org/members", response_model=list[UserOut])
def list_members(ctx: TenantContext = Depends(get_tenant)) -> list[UserOut]:
    users = ctx.db.execute(select(User).order_by(User.created_at)).scalars().all()
    return [
        UserOut(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role.value,
            is_superadmin=u.is_superadmin,
            created_at=u.created_at,
            last_login_at=u.last_login_at,
        )
        for u in users
    ]


@router.get("/org/invitations", response_model=list[InvitationOut])
def list_invitations(ctx: TenantContext = Depends(get_tenant)) -> list[InvitationOut]:
    rows = ctx.db.execute(select(Invitation).order_by(Invitation.created_at.desc())).scalars().all()
    return [InvitationOut(id=i.id, email=i.email, role=i.role.value, created_at=i.created_at,
                          expires_at=i.expires_at, accepted_at=i.accepted_at) for i in rows]


@router.post("/org/invitations", response_model=InviteCreatedOut, status_code=status.HTTP_201_CREATED)
def invite_member(body: InviteIn, ctx: TenantContext = Depends(require_owner)) -> InviteCreatedOut:
    email = str(body.email).lower()
    # Global lookup on purpose: emails are unique across organizations.
    existing = ctx.db.execute(
        select(User.id).where(User.email == email).execution_options(**{SKIP_TENANT_FILTER: True})
    ).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account")
    pending = ctx.db.execute(
        select(Invitation).where(Invitation.email == email, Invitation.accepted_at.is_(None))
    ).scalars().first()
    if pending is not None:
        ctx.db.delete(pending)  # re-inviting replaces the pending invitation (new link, new expiry)
        ctx.db.flush()
    token = new_opaque_token()
    inv = ctx.add(
        Invitation(
            email=email,
            role=UserRole(body.role),
            token_hash=hash_token(token),
            invited_by_user_id=ctx.user.id,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    ctx.db.flush()
    url = f"{get_settings().frontend_origin}/signup?invite={token}"
    get_email_provider().send(
        ctx.db,
        OutboundEmail(
            to=email,
            subject="You've been invited to The Invisible Hand",
            body=f"{ctx.user.email} invited you to join their team on The Invisible Hand.\n\nAccept: {url}\n",
            organization_id=ctx.org_id,
            kind="system",
        ),
    )
    ctx.db.commit()
    return InviteCreatedOut(id=inv.id, email=inv.email, role=inv.role.value, created_at=inv.created_at,
                            expires_at=inv.expires_at, accepted_at=None, invite_url=url)


@router.delete("/org/members/{user_id}", response_model=OkOut)
def remove_member(user_id: int, ctx: TenantContext = Depends(require_owner)) -> OkOut:
    user = ctx.get(User, user_id)
    if user.id == ctx.user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove yourself")
    user.is_active = False
    ctx.db.commit()
    return OkOut()


# --------------------------------------------------------------------------- settings


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    fsc_watchlist: list[str]
    min_margin_pct: float
    max_line_value: float
    set_aside_eligibility: list[str]
    default_overhead_pct: float
    packaging_cost_per_line: float
    payment_terms_days: int
    cost_of_capital_pct: float
    default_freight_per_line: float
    inspection_allowance_per_line: float
    origin_inspection_allowance_per_line: float
    first_article_allowance_per_line: float
    provisional_cost_fraction: float
    min_expected_profit: float
    min_p_win: float
    auto_send_rfq: bool
    voice_enabled: bool
    email_sender_name: str | None
    email_signature: str | None
    # FSCs no organization watched before this save: their last 10 days of RFQs are being loaded now.
    loading_fscs: list[str] = []
    # Open lines whose recommendations are still being recomputed in the background after this save.
    recomputing_lines: int = 0


class SettingsIn(BaseModel):
    fsc_watchlist: list[str] | None = Field(default=None, max_length=100)
    min_margin_pct: float | None = Field(default=None, ge=0, le=5)
    max_line_value: float | None = Field(default=None, gt=0)
    set_aside_eligibility: list[str] | None = None
    default_overhead_pct: float | None = Field(default=None, ge=0, le=5)
    packaging_cost_per_line: float | None = Field(default=None, ge=0)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    cost_of_capital_pct: float | None = Field(default=None, ge=0, le=1)
    default_freight_per_line: float | None = Field(default=None, ge=0)
    inspection_allowance_per_line: float | None = Field(default=None, ge=0)
    origin_inspection_allowance_per_line: float | None = Field(default=None, ge=0)
    first_article_allowance_per_line: float | None = Field(default=None, ge=0)
    provisional_cost_fraction: float | None = Field(default=None, gt=0, le=2)
    min_expected_profit: float | None = Field(default=None, ge=0)
    min_p_win: float | None = Field(default=None, ge=0.05, le=0.9)
    auto_send_rfq: bool | None = None
    voice_enabled: bool | None = None
    email_sender_name: str | None = Field(default=None, max_length=200)
    email_signature: str | None = Field(default=None, max_length=2000)


def _get_settings_row(ctx: TenantContext) -> OrgSettings:
    row = ctx.db.execute(select(OrgSettings)).scalar_one_or_none()
    if row is None:
        row = ctx.add(OrgSettings())
        ctx.db.flush()
    return row


@router.get("/settings", response_model=SettingsOut)
def get_org_settings(ctx: TenantContext = Depends(get_tenant)) -> OrgSettings:
    row = _get_settings_row(ctx)
    ctx.db.commit()
    return row


@router.put("/settings", response_model=SettingsOut)
def update_org_settings(body: SettingsIn, ctx: TenantContext = Depends(get_tenant)) -> SettingsOut:
    import re

    from app.pipelines.ingest import watched_fscs

    row = _get_settings_row(ctx)
    data = body.model_dump(exclude_unset=True)
    watched_before = watched_fscs(ctx.db)
    if "fsc_watchlist" in data and data["fsc_watchlist"] is not None:
        fscs = sorted({f.strip() for f in data["fsc_watchlist"] if f.strip()})
        bad = [f for f in fscs if not re.match(FSC_PATTERN, f)]
        if bad:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid FSC codes: {', '.join(bad)}")
        data["fsc_watchlist"] = fscs
    if data.get("voice_enabled") and not get_settings().voice_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Voice calling is not enabled on this server")
    for k, v in data.items():
        if v is not None or k in ("email_sender_name", "email_signature"):
            setattr(row, k, v)
    ctx.db.commit()
    # Settings change landed costs and margins: refresh this org's recommendations (urgent lines now).
    from app.services.org_bootstrap import refresh_org_soon

    queued = refresh_org_soon(ctx.db, ctx.org_id)
    new_fscs = sorted(set(row.fsc_watchlist or []) - watched_before)
    if new_fscs:
        from app.jobs.queue import enqueue
        from app.jobs.tasks import expand_watchlist_job

        enqueue(expand_watchlist_job, new_fscs)
    out = SettingsOut.model_validate(row)
    out.loading_fscs, out.recomputing_lines = new_fscs, queued
    return out
