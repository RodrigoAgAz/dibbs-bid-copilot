"""Password hashing, JWT access tokens, opaque refresh/reset tokens, auth cookies, CSRF."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Response

from app.config import get_settings

_hasher = PasswordHasher()

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
JWT_ALG = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int, org_id: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": org_id,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_minutes)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=JWT_ALG)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie_kwargs() -> dict:
    s = get_settings()
    return {"secure": s.cookie_secure, "samesite": "lax"}


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> str:
    """Set httpOnly auth cookies and a readable CSRF cookie. Returns the CSRF token."""
    s = get_settings()
    kw = _cookie_kwargs()
    response.set_cookie(
        ACCESS_COOKIE, access_token, httponly=True, max_age=s.access_token_minutes * 60, path="/", **kw
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh_token, httponly=True, max_age=s.refresh_token_days * 86400, path="/api/auth", **kw
    )
    csrf = secrets.token_urlsafe(24)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, max_age=s.refresh_token_days * 86400, path="/", **kw)
    return csrf


def clear_auth_cookies(response: Response) -> None:
    kw = _cookie_kwargs()
    response.delete_cookie(ACCESS_COOKIE, path="/", **kw)
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth", **kw)
    response.delete_cookie(CSRF_COOKIE, path="/", **kw)


def csrf_matches(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)
