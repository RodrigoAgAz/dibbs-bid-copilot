"""Command-line entry points: `python -m app.cli <command>` (used by the Makefile and dev.py)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import typer

from app.config import get_settings

cli = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger("app.cli")


def _setup_logging() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _parse_date(s: str) -> date:
    if s.lower() == "today":
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


@cli.command()
def migrate() -> None:
    """Apply Alembic migrations (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).resolve().parents[1]
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    command.upgrade(cfg, "head")
    typer.echo("migrations applied")


@cli.command()
def bootstrap() -> None:
    """Container start-up: apply migrations, then seed the demo if AUTO_SEED=true and the DB is empty."""
    _setup_logging()
    migrate()
    _maybe_queue_initial_live_load()
    if not get_settings().auto_seed:
        return
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models import Organization

    with session_scope() as db:
        n = db.execute(select(func.count()).select_from(Organization)).scalar_one()
    if n == 0:
        typer.echo("AUTO_SEED: empty database, seeding the demo ...")
        from app.seed.demo import run_seed

        run_seed()
    else:
        typer.echo(f"AUTO_SEED: {n} organization(s) present, skipping seed")


def _maybe_queue_initial_live_load() -> None:
    """AUTO_BACKFILL=true + CONNECTOR_MODE=live: queue the initial live load once (until real history exists)."""
    s = get_settings()
    if not (s.auto_backfill and s.connector_mode == "live"):
        return
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.jobs.queue import enqueue
    from app.jobs.tasks import initial_live_load_job
    from app.models import AwardHistory, IngestCheckpoint

    with session_scope() as db:
        real = db.execute(
            select(func.count()).select_from(AwardHistory).where(AwardHistory.source != "synthetic")
        ).scalar_one()
        marker = db.execute(
            select(IngestCheckpoint).where(IngestCheckpoint.source == "initial_live_load")
        ).scalar_one_or_none()
        if real >= 500:
            typer.echo(f"AUTO_BACKFILL: skipping ({real} real award rows already)")
            return
        if marker is not None and _job_alive(marker.detail):
            typer.echo(f"AUTO_BACKFILL: initial live load already {_job_alive(marker.detail)} (job {marker.detail})")
            return
        job = enqueue(initial_live_load_job, job_timeout=8 * 3600)
        if marker is None:
            db.add(IngestCheckpoint(source="initial_live_load", key="queued", status="done", detail=job))
        else:
            marker.detail = job
    typer.echo(f"AUTO_BACKFILL: queued initial live load (job {job})")


def _job_alive(job_id: str | None) -> str | None:
    """'queued'/'started'/'deferred'/'scheduled' if the RQ job is still pending or running, else None."""
    if not job_id:
        return None
    from app.jobs.queue import get_queue

    q = get_queue()
    if q is None:
        return None
    try:
        from rq.job import Job

        status = Job.fetch(job_id, connection=q.connection).get_status(refresh=True)
    except Exception:  # job expired or unknown
        return None
    value = getattr(status, "value", status)
    return value if value in ("queued", "started", "deferred", "scheduled") else None


@cli.command("export-openapi")
def export_openapi(out: str = typer.Option("openapi.json")) -> None:
    """Write the FastAPI OpenAPI schema to a file (input for openapi-typescript)."""
    from app.main import create_app

    Path(out).write_text(json.dumps(create_app().openapi(), indent=2), encoding="utf-8")
    typer.echo(f"wrote {out}")


@cli.command()
def seed(
    force_synthetic: bool = typer.Option(False, help="Use synthetic history even if real data exists"),
    skip_train: bool = typer.Option(False, help="Skip training the demo model"),
) -> None:
    """Create demo organizations, history, today's RFQs, quotes and a trained model."""
    _setup_logging()
    from app.seed.demo import run_seed

    run_seed(force_synthetic=force_synthetic, skip_train=skip_train)


@cli.command()
def backfill(
    fsc: str = typer.Option("", help="Comma-separated FSCs (default: global default list)"),
    start: str = typer.Option("2023-01-01"),
    end: str = typer.Option("today"),
    mode: str = typer.Option("", help="fixture | live | apify (default: CONNECTOR_MODE)"),
    max_pdfs: int = typer.Option(2000, help="Max RFQ PDFs (one per NSN) fetched this run"),
    max_award_dates: int = typer.Option(10, help="Most recent award posting dates to walk"),
    max_award_pages: int = typer.Option(20, help="Max listing pages per award posting date / NSN search"),
    max_nsn_searches: int = typer.Option(50, help="Max NSN award searches (40 NSNs each) this run"),
) -> None:
    """Resumable historical backfill of awards and procurement history."""
    _setup_logging()
    from app.db import session_scope
    from app.pipelines.backfill import run_backfill

    fscs = [f.strip() for f in fsc.split(",") if f.strip()] or get_settings().default_fsc_watchlist
    with session_scope() as db:
        summary = run_backfill(db, fscs, _parse_date(start), _parse_date(end), mode=mode or None, max_pdfs=max_pdfs,
                               max_award_dates=max_award_dates, max_award_pages=max_award_pages,
                               max_nsn_searches=max_nsn_searches)
    typer.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
def ingest(
    mode: str = typer.Option("", help="fixture | live | apify (default: CONNECTOR_MODE)"),
    day: str = typer.Option("", help="Only this posting date (YYYY-MM-DD)"),
) -> None:
    """Daily RFQ ingest: index files -> solicitations -> details -> predictions -> recommendations."""
    _setup_logging()
    from app.db import session_scope
    from app.pipelines.ingest import run_daily_ingest

    with session_scope() as db:
        summary = run_daily_ingest(db, mode=mode or None, only_date=_parse_date(day) if day else None)
    typer.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
def labels(mode: str = typer.Option("", help="fixture | live (default: CONNECTOR_MODE)")) -> None:
    """Pull newly posted awards and attach them to solicitations."""
    _setup_logging()
    from app.db import session_scope
    from app.pipelines.awards import run_award_labeling

    with session_scope() as db:
        summary = run_award_labeling(db, mode=mode or None)
    typer.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
def train(
    as_of: str = typer.Option("today", help="Evaluation end date T"),
    force_promote: bool = typer.Option(False, help="Promote even if it doesn't beat the active model"),
) -> None:
    """Retrain, evaluate, write ml/reports/latest_model.md and promote if better."""
    _setup_logging()
    from app.db import session_scope
    from app.services.training import train_and_maybe_promote

    with session_scope() as db:
        result = train_and_maybe_promote(db, as_of=_parse_date(as_of), force_promote=force_promote)
    typer.echo(json.dumps(result, indent=2, default=str))


@cli.command("fsc-report")
def fsc_report() -> None:
    """Write ml/reports/fsc_volume.md from the data in the database."""
    _setup_logging()
    from app.db import session_scope
    from app.pipelines.fsc_report import write_fsc_volume_report

    with session_scope() as db:
        path = write_fsc_volume_report(db)
    typer.echo(f"wrote {path}")


@cli.command()
def followups() -> None:
    """Send 48-hour follow-ups and mark overdue quote requests as no_response."""
    _setup_logging()
    from app.db import session_scope
    from app.services.sourcing import run_followups

    with session_scope() as db:
        typer.echo(json.dumps(run_followups(db), indent=2))


@cli.command("create-superadmin")
def create_superadmin(email: str, password: str = typer.Option(..., prompt=True, hide_input=True)) -> None:
    """Create (or promote) a global superadmin user."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db import session_scope
    from app.models import Organization, OrgSettings, User, UserRole

    with session_scope() as db:
        user = db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
        if user is None:
            org = Organization(name="Platform Admin", plan="internal")
            db.add(org)
            db.flush()
            db.add(OrgSettings(organization_id=org.id))
            user = User(organization_id=org.id, email=email.lower(), password_hash=hash_password(password),
                        role=UserRole.owner)
            db.add(user)
        user.is_superadmin = True
    typer.echo(f"{email} is a superadmin")


if __name__ == "__main__":
    cli()
