"""Enqueue jobs on RQ; fall back to a background thread when Redis isn't configured/reachable.

Two queues, each served by its own worker process (see worker.py), so a multi-hour backfill or award
labeling run never delays the 2-hourly RFQ ingest or a quote-request send:
  * default - ingest, follow-ups, quote sends, new-org bootstrap (short, user-facing)
  * bulk    - award labeling, historical backfill, retraining, the initial live load
A job function opts into the bulk queue with the `@bulk` decorator (app.jobs.tasks).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.config import get_settings

log = logging.getLogger(__name__)
QUEUE_NAME = "default"
BULK_QUEUE = "bulk"
QUEUES = (QUEUE_NAME, BULK_QUEUE)
DEFAULT_TIMEOUT = 60 * 60 * 3


def bulk(func: Callable) -> Callable:
    """Mark a job function as long-running: it is enqueued on the bulk queue."""
    func.queue_name = BULK_QUEUE  # type: ignore[attr-defined]
    return func


def queue_for(func: Callable) -> str:
    return getattr(func, "queue_name", QUEUE_NAME)


def redis_connection(timeout: float = 1):
    """A pinged Redis connection, or None when REDIS_URL is unset/unreachable."""
    url = get_settings().redis_url
    if not url:
        return None
    try:
        from redis import Redis

        conn = Redis.from_url(url, socket_connect_timeout=timeout)
        conn.ping()
        return conn
    except Exception as exc:  # pragma: no cover - depends on environment
        log.warning("Redis unavailable (%s)", exc)
        return None


def get_queue(name: str = QUEUE_NAME):
    conn = redis_connection()
    if conn is None:
        return None
    from rq import Queue

    return Queue(name, connection=conn, default_timeout=DEFAULT_TIMEOUT)


# A running job's heartbeat is refreshed every 30 s (RQ job_monitoring_interval); a "started" job without one
# for this long belonged to a worker that was killed (e.g. replaced by a deploy) and will never finish.
STALE_HEARTBEAT = timedelta(minutes=2)


def pending_job_id(func: Callable, now: datetime | None = None) -> str | None:
    """Id of a queued or genuinely running job for `func`, if any (scheduled runs skip instead of piling up)."""
    q = get_queue(queue_for(func))
    if q is None:
        return None
    from rq.job import Job
    from rq.registry import StartedJobRegistry

    now = now or datetime.now(UTC)
    name = f"{func.__module__}.{func.__name__}"
    queued = list(q.job_ids)
    started = list(StartedJobRegistry(queue=q).get_job_ids())
    for job in Job.fetch_many(queued + started, connection=q.connection):
        if job is None or job.func_name != name:
            continue
        if job.id in started:
            beat = job.last_heartbeat or job.started_at
            if beat is not None and beat.tzinfo is None:
                beat = beat.replace(tzinfo=UTC)
            if beat is None or now - beat > STALE_HEARTBEAT:
                log.warning("ignoring abandoned job %s (%s): no heartbeat since %s", job.id, name, beat)
                continue
        return job.id
    return None


def enqueue_unique(func: Callable, *args, **kwargs) -> str | None:
    """Enqueue unless the same job is already queued or running. Used by the scheduler."""
    existing = pending_job_id(func)
    if existing:
        log.info("%s already pending (job %s); not enqueuing another", func.__name__, existing)
        return None
    return enqueue(func, *args, **kwargs)


def enqueue(func: Callable, *args, job_timeout: int | None = None, **kwargs) -> str:
    """Returns a job id ('inline-...' when run in a local thread)."""
    q = get_queue(queue_for(func))
    if q is not None:
        job = q.enqueue(func, *args, job_timeout=job_timeout or DEFAULT_TIMEOUT, **kwargs)
        return job.id
    log.info("running %s in-process (no Redis)", getattr(func, "__name__", func))

    def _run() -> None:
        try:
            func(*args, **kwargs)
        except Exception:
            log.exception("inline job %s failed", getattr(func, "__name__", func))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return f"inline-{getattr(func, '__name__', 'job')}-{t.ident}"
