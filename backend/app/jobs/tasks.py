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


def followups_job() -> dict:
    from app.services.sourcing import run_followups

    with session_scope() as db:
        return run_followups(db)


def daily_digest_job() -> dict:
    from app.services.digest import send_daily_digests

    with session_scope() as db:
        return send_daily_digests(db)


def ops_check_job() -> dict:
    from app.services.ops import run_ops_check

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
