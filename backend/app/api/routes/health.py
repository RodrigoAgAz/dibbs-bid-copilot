from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
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


class HealthCheckOut(BaseModel):
    name: str
    ok: bool
    detail: str


class PipelineRunOut(BaseModel):
    last_success_at: str | None
    last_status: str | None
    last_finished_at: str | None


class AccuracyTierOut(BaseModel):
    n: int | None = None
    median_abs_pct_error: float | None = None
    coverage_80: float | None = None
    coverage_90: float | None = None


class CatalogTierOut(AccuracyTierOut):
    pinball_mean: float | None = None
    baseline_pinball_mean: float | None = None
    pinball_vs_baseline: float | None = None  # 0.22 = the model's pinball loss is 22% lower than the baseline's
    baseline_median_abs_pct_error: float | None = None
    baseline_coverage_80: float | None = None


class CatalogAccuracyOut(CatalogTierOut):
    """The product's own supply classes alone (model vs baseline, same test window)."""

    fscs: list[str] = []
    by_fsc: dict[str, CatalogTierOut | None] = {}


class ActiveModelOut(BaseModel):
    run_id: int
    trained_at: str | None
    pinball_mean: float | None
    baseline_pinball_mean: float | None = None
    test_rows: int | None = None
    test_window: list[str | None] = []
    median_abs_pct_error: float | None = None
    coverage_80: float | None = None
    coverage_90: float | None = None
    baseline_median_abs_pct_error: float | None = None
    real_data: bool = False
    model_version: str | None = None
    with_history: AccuracyTierOut | None = None
    new_parts: AccuracyTierOut | None = None
    by_history: dict[str, AccuracyTierOut | None] = {}
    catalog: CatalogAccuracyOut | None = None


class TrackRecordOut(BaseModel):
    n: int
    coverage_80: float | None
    median_abs_pct_error: float | None
    since_days: int
    since: str | None = None


class DataHealthOut(BaseModel):
    status: str  # ok | degraded
    checked_at: str
    connector_mode: str
    newest_rfq_posting_date: str | None
    open_rfq_lines: int
    award_rows: int
    award_rows_last_30_days: int
    nsns_with_history: int
    award_rows_by_source: dict[str, int] = {}
    model: ActiveModelOut | None
    live_track_record: TrackRecordOut | None = None
    runs: dict[str, PipelineRunOut]
    checks: list[HealthCheckOut]


@router.get("/api/health/data", response_model=DataHealthOut)
def data_health(db: Session = Depends(get_db)) -> dict:
    """Public data freshness (DIBBS-derived counts and pipeline timestamps only; no tenant data).

    Drives the "data updated" indicator in the app and lets operators check a deployment without logging in.
    """
    from app.services.ops import data_health as compute

    return compute(db)
