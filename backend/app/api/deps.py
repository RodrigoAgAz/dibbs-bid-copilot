"""Shared FastAPI dependencies: current user, tenant context, role guards."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import ACCESS_COOKIE, decode_access_token
from app.core.tenancy import TenantContext, bind_session_to_org
from app.db import get_db
from app.models import User, UserRole


def _token_from_request(request: Request) -> str | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    payload = decode_access_token(token) if token else None
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active or user.organization_id != payload.get("org"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def get_tenant(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TenantContext:
    """THE tenancy dependency. Every tenant-scoped endpoint depends on this."""
    bind_session_to_org(db, user.organization_id)
    return TenantContext(db=db, user=user)


def require_owner(ctx: TenantContext = Depends(get_tenant)) -> TenantContext:
    if ctx.user.role != UserRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
    return ctx


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superadmin required")
    return user
