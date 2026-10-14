"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from app.config import get_settings
from app.core.security import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER, REFRESH_COOKIE, csrf_matches

# Unauthenticated endpoints (no session to ride on) and signature-verified webhooks.
CSRF_EXEMPT_PREFIXES = (
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/accept-invite",
    "/api/webhooks/",
)
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(
        title="DIBBS Bid Copilot API",
        version="0.1.0",
        description="Bid recommendations for DLA DIBBS solicitations. The app never submits bids.",
    )

    # The dashboard returns every open line in the org's FSCs (thousands in live mode): compress it.
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", CSRF_HEADER],
    )

    @app.middleware("http")
    async def csrf_protect(request: Request, call_next):
        """Double-submit-cookie CSRF check for cookie-authenticated mutating requests."""
        if request.method in MUTATING and request.url.path.startswith("/api/"):
            path = request.url.path
            has_session_cookie = ACCESS_COOKIE in request.cookies or REFRESH_COOKIE in request.cookies
            if (
                has_session_cookie
                and not path.startswith(CSRF_EXEMPT_PREFIXES)
                and not csrf_matches(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER))
            ):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return await call_next(request)

    from app.api.routes import auth, health, org, suppliers

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(org.router)
    app.include_router(suppliers.router)

    _include_optional_routers(app)
    return app


def _include_optional_routers(app: FastAPI) -> None:
    """Routers added in later build phases."""
    import importlib

    for name in ("dashboard", "rfq", "quotes", "history", "admin", "webhooks"):
        try:
            mod = importlib.import_module(f"app.api.routes.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"app.api.routes.{name}":
                continue
            raise
        app.include_router(mod.router)


app = create_app()
