"""Background job functions (run by the RQ worker, or inline when Redis is unavailable).

Long-running jobs are marked `@bulk` and run on their own queue/worker process (see app.jobs.queue).
"""

from __future__ import annotations

import logging
from datetime import date

from app.db import session_scope
from app.jobs.queue import bulk

log = logging.getLogger(__name__)


def daily_ingest_job(mode: str | None = None) -> dict:
    from app.pipelines.ingest import run_daily_ingest

    with session_scope() as db:
        return run_daily_ingest(db, mode=mode)


@bulk
def award_labeling_job(mode: str | None = None) -> dict:
    from app.pipelines.awards import run_award_labeling

    with session_scope() as db:
        return run_award_labeling(db, mode=mode)


@bulk
def retrain_job() -> dict:
    from app.services.training import train_and_maybe_promote

    with session_scope() as db:
        return train_and_maybe_promote(db, as_of=date.today())


@bulk
def nightly_backfill_job() -> dict:
    """Grow the price history a little every night: PDFs for NSNs not yet covered, older award postings,
    NSN award searches. Resumes from checkpoints; bounded to well under an hour at 1 request/second."""
    from app.config import get_settings
    from app.pipelines.backfill import run_backfill
    from app.pipelines.ingest import watched_fscs

    s = get_settings()
    if s.connector_mode == "fixture" or not s.nightly_backfill:
        return {"skipped": True}
    with session_scope() as db:
        fscs = sorted(watched_fscs(db) | set(s.default_fsc_watchlist))
        out = run_backfill(db, fscs, date(2023, 1, 1), date.today(), max_pdfs=250, max_award_dates=3,
                           max_award_pages=10, max_nsn_searches=10)
    log.info("nightly backfill: %s", {k: out.get(k) for k in ("status", "nsn_pdfs", "history_rows", "award_rows")})
    return out


def expand_watchlist_job(fscs: list[str]) -> dict:
    """Default queue (serialized with the ingest): it upserts the same tables. Records each class as loaded, so
    queue_new_fsc_loads_if_needed never loads it again (even when DIBBS had no recent RFQs for it)."""
    from sqlalchemy import select

    from app.models import IngestCheckpoint
    from app.pipelines.ingest import ingest_new_fscs

    with session_scope() as db:
        out = ingest_new_fscs(db, set(fscs))
        done = {k for (k,) in db.execute(
            select(IngestCheckpoint.key).where(IngestCheckpoint.source == "fsc_load", IngestCheckpoint.key.in_(fscs))
        ).all()}
        for f in sorted(set(fscs) - done):
            db.add(IngestCheckpoint(source="fsc_load", key=f, status="done", detail=str(out.get("new_lines"))))
        return out


def queue_new_fsc_loads_if_needed() -> list[str]:
    """Live mode: watched supply classes never loaded (no line on file, no load recorded) get their recent RFQs and,
    on the bulk queue, their price history. Not a job itself: the worker calls it at startup (on the newest code) and
    the hourly ops check, so a load queued during a deploy and lost to it is picked up again."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.jobs.queue import enqueue_unique
    from app.models import IngestCheckpoint, SolicitationLine
    from app.pipelines.ingest import watched_fscs

    if get_settings().connector_mode != "live":
        return []  # fixture/demo databases load a newly watched class from the settings change itself
    with session_scope() as db:
        watched = watched_fscs(db)
        loaded = {k for (k,) in db.execute(select(IngestCheckpoint.key).where(IngestCheckpoint.source == "fsc_load")).all()}
        with_lines = {f for (f,) in db.execute(
            select(SolicitationLine.fsc).where(SolicitationLine.fsc.in_(watched)).distinct()
        ).all()}
    missing = sorted(watched - loaded - with_lines)
    if missing:
        enqueue_unique(expand_watchlist_job, missing)
        enqueue_unique(catalog_backfill_job, missing, job_timeout=8 * 3600)
        log.info("never-loaded watched FSCs %s: queued their recent RFQs and history", missing)
    return missing


@bulk
def catalog_backfill_job(fscs: list[str]) -> dict:
    """Supply classes a live deployment just started watching: their PDF procurement history and recent awards, then a
    retrain (bounded like the initial live load; the nightly backfill keeps growing it). Promotion rules apply as usual."""
    from app.pipelines.backfill import run_backfill
    from app.services.training import train_and_maybe_promote

    out: dict = {}
    with session_scope() as db:
        out["backfill"] = run_backfill(db, sorted(fscs), date(2023, 1, 1), date.today(), mode="live", max_pdfs=800,
                                       max_award_dates=2, max_award_pages=5, max_nsn_searches=5)
    with session_scope() as db:
        out["train"] = train_and_maybe_promote(db, as_of=date.today())
    log.info("catalog backfill %s: %s", sorted(fscs), {k: v.get("status", v.get("promoted")) for k, v in out.items()})
    return out


def followups_job() -> dict:
    from app.services.sourcing import run_followups

    with session_scope() as db:
        return run_followups(db)


def daily_digest_job() -> dict:
    from app.services.digest import send_daily_digests

    with session_scope() as db:
        return send_daily_digests(db)


def refresh_all_recommendations_job(version: str) -> dict:
    """One-off after a recommendation-rules change. Runs on the default queue, serialized with the ingest,
    so the two never upsert the same recommendations concurrently."""
    from app.models import IngestCheckpoint
    from app.services.recommendations import ENGINE_VERSION, refresh_all_recommendations

    if version != ENGINE_VERSION:
        # Queued by newer code but picked up by a worker still running older code (deploy overlap): computing
        # here would stamp the new version on old rules. Leave it for the new worker (hourly ops check).
        log.warning("engine refresh v%s skipped: this worker runs engine v%s", version, ENGINE_VERSION)
        return {"skipped": f"worker runs engine v{ENGINE_VERSION}"}
    with session_scope() as db:
        out = refresh_all_recommendations(db)
        row = db.query(IngestCheckpoint).filter_by(source="recommendation_engine", key="version").one_or_none()
        if row is None:
            db.add(IngestCheckpoint(source="recommendation_engine", key="version", status="done", detail=version))
        else:
            row.detail = version
    log.info("recommendations recomputed for engine v%s: %s", version, out)
    return out


def queue_engine_refresh_if_needed() -> str | None:
    """Queue refresh_all_recommendations_job when the stored engine version is behind (not a job itself).

    Called at boot and by the hourly ops check, so a refresh killed by a redeploy is picked up again.
    """
    from sqlalchemy import select

    from app.jobs.queue import enqueue_unique
    from app.models import IngestCheckpoint, Recommendation
    from app.services.recommendations import ENGINE_VERSION

    with session_scope() as db:
        if db.execute(select(Recommendation.id).limit(1)).first() is None:
            return None  # nothing to recompute (fresh database; the seed computes with the current engine)
        row = db.execute(
            select(IngestCheckpoint).where(IngestCheckpoint.source == "recommendation_engine",
                                           IngestCheckpoint.key == "version")
        ).scalar_one_or_none()
        if row is not None and row.detail == ENGINE_VERSION:
            return None
    return enqueue_unique(refresh_all_recommendations_job, ENGINE_VERSION, job_timeout=2 * 3600)


def demo_refresh_job() -> list:
    """Simulated supplier quotes on real lines for demo orgs (default queue: serialized with the ingest)."""
    from app.services.demo_refresh import refresh_demo_orgs

    with session_scope() as db:
        return refresh_demo_orgs(db)


# .1: re-score the active model by history depth; .2: recent-data protocol + deployed refit (held-out twin kept)
MODEL_CODE_VERSION = "lgbm_quantile_v2.2"  # bump when the training code changes: one retrain is queued


def queue_retrain_if_model_code_changed() -> str | None:
    """Queue one retrain (bulk) after a deploy with new model code; promotion still needs a better test score."""
    from sqlalchemy import select

    from app.jobs.queue import enqueue_unique
    from app.models import IngestCheckpoint, ModelRun

    with session_scope() as db:
        if db.execute(select(ModelRun.id).limit(1)).first() is None:
            return None
        row = db.execute(select(IngestCheckpoint).where(IngestCheckpoint.source == "model_code",
                                                        IngestCheckpoint.key == "version")).scalar_one_or_none()
        if row is not None and row.detail == MODEL_CODE_VERSION:
            return None
    return enqueue_unique(retrain_for_model_code_job)


@bulk
def retrain_for_model_code_job() -> dict:
    """Retrain once for new model code. The marker is set here, by the new code, so an outgoing worker still
    running older code (deploy overlap) can't mark it done: it doesn't have this function and the job fails."""
    from app.models import IngestCheckpoint

    with session_scope() as db:
        row = db.query(IngestCheckpoint).filter_by(source="model_code", key="version").one_or_none()
        if row is None:
            db.add(IngestCheckpoint(source="model_code", key="version", status="done", detail=MODEL_CODE_VERSION))
        else:
            row.detail = MODEL_CODE_VERSION
    return retrain_job()


@bulk
def research_job(run_id: str) -> dict:
    """In-cluster pricing research (results in the worker logs). The marker is set by this new code."""
    from app.models import IngestCheckpoint
    from app.services.research import run_research

    with session_scope() as db:
        if db.query(IngestCheckpoint).filter_by(source="research", key=run_id).one_or_none() is None:
            db.add(IngestCheckpoint(source="research", key=run_id, status="done", detail="started"))
    with session_scope() as db:
        return run_research(db, run_id)


def queue_research_if_requested() -> str | None:
    from app.config import get_settings
    from app.jobs.queue import enqueue_unique
    from app.models import IngestCheckpoint

    run_id = get_settings().research_run.strip()
    if not run_id:
        return None
    with session_scope() as db:
        if db.query(IngestCheckpoint).filter_by(source="research", key=run_id).one_or_none() is not None:
            return None
    return enqueue_unique(research_job, run_id, job_timeout=4 * 3600)


def ops_check_job() -> dict:
    from app.services.ops import run_ops_check

    queue_engine_refresh_if_needed()
    queue_retrain_if_model_code_changed()
    queue_new_fsc_loads_if_needed()
    with session_scope() as db:
        return run_ops_check(db)


def send_quote_request_job(quote_request_id: int) -> dict:
    from app.services.sourcing import send_quote_request_by_id

    with session_scope() as db:
        return send_quote_request_by_id(db, quote_request_id)


@bulk
def initial_live_load_job() -> dict:
    """First load for a live deployment: recent RFQs, PDF procurement history, recent awards, then train.

    Bounded so it finishes in roughly an hour at 1 request/second; later runs of the backfill resume
    from checkpoints.
    """
    from app.config import get_settings
    from app.pipelines.backfill import run_backfill
    from app.pipelines.ingest import run_daily_ingest
    from app.services.training import train_and_maybe_promote

    out: dict = {}
    with session_scope() as db:
        out["ingest"] = run_daily_ingest(db, mode="live")
    with session_scope() as db:
        out["backfill"] = run_backfill(
            db, get_settings().default_fsc_watchlist, date(2023, 1, 1), date.today(), mode="live",
            max_pdfs=800, max_award_dates=2, max_award_pages=5, max_nsn_searches=5,
        )
    with session_scope() as db:
        out["train"] = train_and_maybe_promote(db, as_of=date.today(), force_promote=True)
    log.info("initial live load finished: %s", {k: v.get("status", v.get("promoted")) for k, v in out.items()})
    return out
