"""Account hardening for internet-facing deployments (run by `python -m app.cli bootstrap` at every boot).

* DEMO_PASSWORD_LOGIN=false: the seeded demo accounts (whose passwords are published in DEMO_CREDENTIALS.md)
  get random password hashes and their sessions are revoked, so nobody can sign in with them.
* PUBLIC_DEMO=true: a read-only viewer exists in the Atlas demo org; POST /api/auth/demo signs visitors in as it.
* BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD: that superadmin exists, with that password.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import hash_password, verify_password
from app.models import Organization, OrgSettings, RefreshToken, User, UserRole

log = logging.getLogger(__name__)

DEMO_VIEWER_EMAIL = "demo@atlas-demo.example.com"
DEMO_VIEWER_ORG = "Atlas Federal Supply (demo)"


def _seeded_demo_accounts() -> list[tuple[str, str]]:
    from app.seed.demo import DEMO_ORGS, SUPERADMIN

    return [(o["email"], o["password"]) for o in DEMO_ORGS] + [(SUPERADMIN["email"], SUPERADMIN["password"])]


def lock_demo_passwords(db: Session) -> int:
    """Replace the published demo passwords with random ones and revoke those users' sessions."""
    locked = 0
    for email, published in _seeded_demo_accounts():
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None or not verify_password(published, user.password_hash):
            continue
        user.password_hash = hash_password(secrets.token_urlsafe(32))
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        locked += 1
    db.flush()
    return locked


def ensure_demo_viewer(db: Session) -> User | None:
    """The read-only account behind "Explore the demo" (None when the demo org isn't seeded)."""
    org = db.execute(select(Organization).where(Organization.name == DEMO_VIEWER_ORG)).scalar_one_or_none()
    if org is None:
        return None
    user = db.execute(select(User).where(User.email == DEMO_VIEWER_EMAIL)).scalar_one_or_none()
    if user is None:
        user = User(organization_id=org.id, email=DEMO_VIEWER_EMAIL, full_name="Demo visitor",
                    password_hash=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
    user.organization_id = org.id
    user.role = UserRole.viewer
    user.is_superadmin = False
    user.is_active = True
    db.flush()
    return user


def ensure_bootstrap_admin(db: Session, email: str, password: str) -> User:
    email = email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        org = db.execute(
            select(Organization).where(Organization.name == "Platform Admin", Organization.plan == "internal")
        ).scalars().first()
        if org is None:
            org = Organization(name="Platform Admin", plan="internal")
            db.add(org)
            db.flush()
            db.add(OrgSettings(organization_id=org.id))
        user = User(organization_id=org.id, email=email, full_name="Platform Admin", role=UserRole.owner,
                    password_hash=hash_password(password))
        db.add(user)
    elif not verify_password(password, user.password_hash):
        user.password_hash = hash_password(password)
    user.is_superadmin = True
    user.is_active = True
    db.flush()
    return user


def secure_accounts(db: Session) -> dict:
    s = get_settings()
    out: dict = {}
    if not s.demo_password_login:
        out["demo_passwords_locked"] = lock_demo_passwords(db)
    if s.public_demo:
        out["demo_viewer"] = ensure_demo_viewer(db) is not None
    if s.bootstrap_admin_email and s.bootstrap_admin_password:
        if len(s.bootstrap_admin_password) < 12:
            log.warning("BOOTSTRAP_ADMIN_PASSWORD is shorter than 12 characters; not applied")
        else:
            ensure_bootstrap_admin(db, s.bootstrap_admin_email, s.bootstrap_admin_password)
            out["bootstrap_admin"] = True
    return out
