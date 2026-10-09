from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/api/health/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    redis_ok: bool | None = None
    if s.redis_url:
        try:
            import redis

            redis.Redis.from_url(s.redis_url, socket_connect_timeout=0.5).ping()
            redis_ok = True
        except Exception:
            redis_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "redis": redis_ok,
        "demo_mode": s.demo_mode,
        "connector_mode": s.connector_mode,
        "llm_provider": s.effective_llm_provider,
        "email_provider": s.email_provider,
        "voice_enabled": s.voice_enabled,
    }
