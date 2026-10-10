"""Enqueue jobs on RQ; fall back to a background thread when Redis isn't configured/reachable."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.config import get_settings

log = logging.getLogger(__name__)
QUEUE_NAME = "default"


def get_queue():
    url = get_settings().redis_url
    if not url:
        return None
    try:
        from redis import Redis
        from rq import Queue

        conn = Redis.from_url(url, socket_connect_timeout=1)
        conn.ping()
        return Queue(QUEUE_NAME, connection=conn, default_timeout=60 * 60 * 3)
    except Exception as exc:  # pragma: no cover - depends on environment
        log.warning("Redis unavailable (%s); running jobs in-process", exc)
        return None


def enqueue(func: Callable, *args, job_timeout: int | None = None, **kwargs) -> str:
    """Returns a job id ('inline-...' when run in a local thread)."""
    q = get_queue()
    if q is not None:
        job = q.enqueue(func, *args, job_timeout=job_timeout or q._default_timeout, **kwargs)
        return job.id

    def _run() -> None:
        try:
            func(*args, **kwargs)
        except Exception:
            log.exception("inline job %s failed", getattr(func, "__name__", func))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return f"inline-{getattr(func, '__name__', 'job')}-{t.ident}"
