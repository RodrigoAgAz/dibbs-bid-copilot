"""RQ worker entry point: `python -m app.jobs.worker` (Linux/Docker; RQ needs fork).

Runs one worker process per pool so long bulk jobs never block the default queue. WORKER_POOLS is a
';'-separated list of pools, each a ','-separated list of queues in priority order
(default "default;bulk": one process for `default`, one for `bulk`). A single pool "default,bulk"
gives the old one-process behaviour. If any pool process exits, the others are stopped and the
container exits non-zero so the platform restarts it.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import sys
import time
import uuid

from app.config import get_settings
from app.jobs.queue import BULK_QUEUE, QUEUE_NAME

log = logging.getLogger("app.jobs.worker")


def parse_pools(spec: str) -> list[list[str]]:
    pools = [[q.strip() for q in pool.split(",") if q.strip()] for pool in spec.split(";")]
    return [p for p in pools if p] or [[QUEUE_NAME, BULK_QUEUE]]


def _logging() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_pool(queues: list[str]) -> None:
    from redis import Redis
    from rq import Queue, Worker

    _logging()
    conn = Redis.from_url(get_settings().redis_url)
    # Unique per process: PIDs repeat across container restarts, and a killed container's worker stays
    # registered in Redis until its TTL expires, so pid-based names collide ("active worker named ...").
    name = f"{'+'.join(queues)}-{uuid.uuid4().hex[:10]}"
    Worker([Queue(q, connection=conn) for q in queues], connection=conn, name=name).work(with_scheduler=False)


STARTUP_JOBS_DELAY = 60  # seconds: let the previous deployment's worker shut down first


def _queue_startup_jobs() -> None:
    """Jobs that must run on the newest code: a pending recommendation-engine refresh and the demo top-up.

    Queued after a delay because during a deploy the outgoing worker is still listening for a while and would
    pick them up with its older code (the engine refresh also refuses to run on older code).
    """
    time.sleep(STARTUP_JOBS_DELAY)
    try:
        from app.jobs.queue import enqueue_unique
        from app.jobs.tasks import demo_refresh_job, queue_engine_refresh_if_needed

        queue_engine_refresh_if_needed()
        enqueue_unique(demo_refresh_job)  # cheap when the demo is already topped up
    except Exception:  # never take the worker down
        log.exception("startup jobs could not be queued")


def main() -> None:
    _logging()
    import threading

    startup = threading.Thread(target=_queue_startup_jobs, name="startup-jobs", daemon=True)
    pools = parse_pools(os.environ.get("WORKER_POOLS", f"{QUEUE_NAME};{BULK_QUEUE}"))
    if len(pools) == 1:
        startup.start()
        run_pool(pools[0])
        return
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=run_pool, args=(p,), name="+".join(p), daemon=False) for p in pools]
    for p in procs:
        p.start()
        log.info("worker pool %s started (pid %s)", p.name, p.pid)
    startup.start()  # after forking: never fork while another thread may hold a lock

    stopping = False

    def _stop(signum, _frame):
        nonlocal stopping
        stopping = True
        for p in procs:
            if p.is_alive():
                os.kill(p.pid, signum)  # RQ: first SIGTERM/SIGINT = warm shutdown (finish the current job)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stopping and all(p.is_alive() for p in procs):
        time.sleep(2)
    if not stopping:
        dead = [p.name for p in procs if not p.is_alive()]
        log.error("worker pool(s) %s exited; stopping the others", dead)
        _stop(signal.SIGTERM, None)
    for p in procs:
        p.join()
    sys.exit(0 if stopping else 1)


if __name__ == "__main__":
    main()
