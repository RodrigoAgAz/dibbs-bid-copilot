"""Superadmin: ingestion runs, model runs/metrics, manual triggers, cross-org review queue."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_superadmin
from app.api.routes.quotes import build_review_items
from app.config import REPO_ROOT, get_settings
from app.db import get_db
from app.jobs import tasks
from app.jobs.queue import enqueue
from app.models import (
    AwardHistory,
    IngestionRun,
    ModelRun,
    Organization,
    QuoteRequest,
    Solicitation,
    SolicitationLine,
    SupplierQuote,
    User,
)
from app.schemas.rfq import ReviewItem

router = APIRouter(prefix="/api/admin", tags=["admin"])
REPORTS_DIR = REPO_ROOT / "ml" / "reports"


class IngestionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_ingested: int
    errors: list[Any]
    details: dict[str, Any]


class ModelRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    model_type: str
    train_window: str
    test_window: str
    metrics: dict[str, Any]
    is_active: bool
    notes: str | None


class JobOut(BaseModel):
    job_id: str
    queued: str


class OverviewOut(BaseModel):
    organizations: int
    users: int
    solicitations: int
    open_lines: int
    awards: int
    nsns_with_history: int
    pending_review: int
    connector_mode: str
    demo_mode: bool
    llm_provider: str
    email_provider: str
    voice_enabled: bool


class ReviewDecision(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    unit_price: float | None = Field(default=None, gt=0)


@router.get("/overview", response_model=OverviewOut)
def overview(_: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> OverviewOut:
    from app.core.timeutil import today_et

    s = get_settings()
    count = lambda stmt: int(db.execute(stmt).scalar_one())  # noqa: E731
    return OverviewOut(
        organizations=count(select(func.count()).select_from(Organization)),
        users=count(select(func.count()).select_from(User)),
        solicitations=count(select(func.count()).select_from(Solicitation)),
        open_lines=count(
            select(func.count()).select_from(SolicitationLine).join(Solicitation)
            .where(Solicitation.return_by_date >= today_et())
        ),
        awards=count(select(func.count()).select_from(AwardHistory)),
        nsns_with_history=count(select(func.count(func.distinct(AwardHistory.nsn)))),
        pending_review=count(
            select(func.count()).select_from(SupplierQuote)
            .where(SupplierQuote.needs_review.is_(True), SupplierQuote.reviewed_at.is_(None))
        ),
        connector_mode=s.connector_mode,
        demo_mode=s.demo_mode,
        llm_provider=s.effective_llm_provider,
        email_provider=s.email_provider,
        voice_enabled=s.voice_enabled,
    )


@router.get("/ingestion-runs", response_model=list[IngestionRunOut])
def ingestion_runs(_: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> list[IngestionRun]:
    return list(db.execute(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(100)).scalars())


@router.get("/model-runs", response_model=list[ModelRunOut])
def model_runs(_: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> list[ModelRun]:
    return list(db.execute(select(ModelRun).order_by(ModelRun.created_at.desc()).limit(50)).scalars())


@router.post("/model-runs/{run_id}/activate", response_model=ModelRunOut)
def activate_model(run_id: int, _: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> ModelRun:
    from app.services.pricing import clear_model_cache

    run = db.get(ModelRun, run_id)
    has_file = bool(run and run.artifact_path and Path(run.artifact_path).exists())
    has_blob = bool(run and db.execute(select(ModelRun.artifact_blob.is_not(None)).where(ModelRun.id == run_id)).scalar())
    if run is None or not (has_file or has_blob):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model run (or its artifact) not found")
    for r in db.execute(select(ModelRun).where(ModelRun.is_active.is_(True))).scalars():
        r.is_active = False
    run.is_active = True
    db.commit()
    clear_model_cache()
    return run


@router.post("/ingest", response_model=JobOut)
def trigger_ingest(_: User = Depends(require_superadmin)) -> JobOut:
    return JobOut(job_id=enqueue(tasks.daily_ingest_job), queued="daily_ingest")


@router.post("/labels", response_model=JobOut)
def trigger_labels(_: User = Depends(require_superadmin)) -> JobOut:
    return JobOut(job_id=enqueue(tasks.award_labeling_job), queued="award_labeling")


@router.post("/train", response_model=JobOut)
def trigger_train(_: User = Depends(require_superadmin)) -> JobOut:
    return JobOut(job_id=enqueue(tasks.retrain_job), queued="retrain")


@router.post("/followups", response_model=JobOut)
def trigger_followups(_: User = Depends(require_superadmin)) -> JobOut:
    return JobOut(job_id=enqueue(tasks.followups_job), queued="followups")


@router.get("/review-queue", response_model=list[ReviewItem])
def admin_review_queue(_: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> list[ReviewItem]:
    return build_review_items(db)


@router.post("/supplier-quotes/{quote_id}/review", response_model=ReviewItem | None)
def review_quote(quote_id: int, body: ReviewDecision, _: User = Depends(require_superadmin),
                 db: Session = Depends(get_db)) -> None:
    from app.services.recommendations import refresh_line_for_org

    q = db.get(SupplierQuote, quote_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    if body.action == "approve":
        if body.unit_price:
            q.unit_price = body.unit_price
        q.needs_review = False
    else:
        q.declined = True  # rejected quotes never count toward the cost floor
    q.reviewed_at = datetime.now(UTC)
    qr = db.get(QuoteRequest, q.quote_request_id)
    refresh_line_for_org(db, q.organization_id, db.get(SolicitationLine, qr.solicitation_line_id))
    db.commit()
    return None


@router.get("/reports/{name}")
def report_file(name: str, _: User = Depends(require_superadmin)) -> FileResponse:
    allowed = {"latest_model.md", "fsc_volume.md", "calibration.png", "coverage.png", "pinball.png", "backtest.png"}
    if name not in allowed or not (REPORTS_DIR / name).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return FileResponse(REPORTS_DIR / name)
