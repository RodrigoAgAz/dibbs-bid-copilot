"""Signup, login, logout, refresh, password reset, invitation acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.core.ratelimit import client_ip, enforce
from app.core.security import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    set_auth_cookies,
    verify_password,
)
from app.db import get_db
from app.models import Invitation, Organization, OrgSettings, PasswordResetToken, RefreshToken, User, UserRole
from app.schemas.auth import (
    AcceptInviteIn,
    AuthOptionsOut,
    ForgotPasswordIn,
    LoginIn,
    MeOut,
    OkOut,
    OrganizationOut,
    PreferencesIn,
    ResetPasswordIn,
    SignupIn,
    UserOut,
)
from app.services.email.providers import OutboundEmail, get_email_provider
from app.services.org_bootstrap import on_organization_created

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_session(db: Session, response: Response, user: User) -> None:
    s = get_settings()
    refresh = new_opaque_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days),
        )
    )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    set_auth_cookies(response, create_access_token(user.id, user.organization_id), refresh)


def _me(db: Session, user: User) -> MeOut:
    org = db.get(Organization, user.organization_id)
    s = get_settings()
    return MeOut(
        user=UserOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role.value,
            is_superadmin=user.is_superadmin,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            email_digest=user.email_digest,
        ),
        organization=OrganizationOut.model_validate(org),
        voice_globally_enabled=s.voice_enabled,
        demo_mode=s.demo_mode,
        email_delivery=s.email_provider == "resend" and bool(s.resend_api_key),
    )


@router.post("/signup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    enforce(request, "signup", str(body.email), limit=5, window_s=3600)
    email = str(body.email).lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    org = Organization(name=body.organization_name.strip(), cage_code=(body.cage_code or None))
    db.add(org)
    db.flush()
    db.add(OrgSettings(organization_id=org.id))
    user = User(
        organization_id=org.id,
        email=email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=UserRole.owner,
    )
    db.add(user)
    db.commit()
    on_organization_created(db, org.id)
    _issue_session(db, response, user)
    return _me(db, user)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    enforce(request, "login", str(body.email), limit=10, window_s=300)
    user = db.execute(select(User).where(User.email == str(body.email).lower())).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    _issue_session(db, response, user)
    return _me(db, user)


@router.get("/options", response_model=AuthOptionsOut)
def options() -> AuthOptionsOut:
    """What the login page should offer (public)."""
    return AuthOptionsOut(public_demo=get_settings().public_demo)


@router.post("/demo", response_model=MeOut)
def demo_login(request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    """"Explore the demo": a read-only session in the Atlas demo org (PUBLIC_DEMO=true only)."""
    if not get_settings().public_demo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The public demo is not enabled")
    enforce(request, "demo", client_ip(request), limit=20, window_s=3600)
    from app.services.accounts import ensure_demo_viewer

    user = ensure_demo_viewer(db)
    if user is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The demo isn't available right now")
    _issue_session(db, response, user)
    return _me(db, user)


@router.post("/refresh", response_model=OkOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> OkOut:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    row = db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or row.expires_at < now:
        clear_auth_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    row.revoked_at = now  # rotate
    _issue_session(db, response, user)
    return OkOut()


@router.post("/logout", response_model=OkOut)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> OkOut:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        row = db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))
        ).scalar_one_or_none()
        if row and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            db.commit()
    clear_auth_cookies(response)
    return OkOut()


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeOut:
    return _me(db, user)


@router.put("/me/preferences", response_model=MeOut)
def update_preferences(body: PreferencesIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeOut:
    user.email_digest = body.email_digest
    db.commit()
    return _me(db, user)


@router.post("/forgot-password", response_model=OkOut)
def forgot_password(body: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)) -> OkOut:
    enforce(request, "reset", str(body.email), limit=5, window_s=3600)
    user = db.execute(select(User).where(User.email == str(body.email).lower())).scalar_one_or_none()
    if user is not None and user.is_active:
        token = new_opaque_token()
        db.add(
            PasswordResetToken(
                user_id=user.id, token_hash=hash_token(token), expires_at=datetime.now(UTC) + timedelta(hours=1)
            )
        )
        link = f"{get_settings().frontend_origin}/reset-password?token={token}"
        get_email_provider().send(
            db,
            OutboundEmail(
                to=user.email,
                subject="Reset your DIBBS Bid Copilot password",
                body=(
                    "Someone asked to reset the password for this account.\n\n"
                    f"Reset it here (valid for 1 hour): {link}\n\n"
                    "If this wasn't you, you can ignore this email."
                ),
                organization_id=user.organization_id,
                kind="system",
            ),
        )
        db.commit()
    # Always the same answer, so the endpoint can't be used to discover accounts.
    return OkOut(detail="If that email has an account, a reset link is on its way.")


@router.post("/reset-password", response_model=OkOut)
def reset_password(body: ResetPasswordIn, request: Request, db: Session = Depends(get_db)) -> OkOut:
    enforce(request, "reset-submit", body.token[:16], limit=10, window_s=3600)
    row = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(body.token))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    user.password_hash = hash_password(body.password)
    row.used_at = now
    # Revoke all sessions after a password change.
    for rt in db.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)).scalars():
        if rt.revoked_at is None:
            rt.revoked_at = now
    db.commit()
    return OkOut(detail="Password updated. You can log in now.")


@router.post("/accept-invite", response_model=MeOut)
def accept_invite(body: AcceptInviteIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    enforce(request, "invite", body.token[:16], limit=10, window_s=3600)
    inv = db.execute(select(Invitation).where(Invitation.token_hash == hash_token(body.token))).scalar_one_or_none()
    now = datetime.now(UTC)
    if inv is None or inv.accepted_at is not None or inv.expires_at < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is invalid or has expired")
    if db.execute(select(User).where(User.email == inv.email)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(
        organization_id=inv.organization_id,
        email=inv.email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=inv.role,
    )
    db.add(user)
    inv.accepted_at = now
    db.commit()
    _issue_session(db, response, user)
    return _me(db, user)
