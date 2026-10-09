"""RQ worker entry point: `python -m app.jobs.worker` (Linux/Docker; RQ needs fork)."""

from __future__ import annotations

import logging

from redis import Redis
from rq import Queue, Worker

from app.config import get_settings
from app.jobs.queue import QUEUE_NAME


def main() -> None:
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = Redis.from_url(s.redis_url)
    Worker([Queue(QUEUE_NAME, connection=conn)], connection=conn).work(with_scheduler=False)


if __name__ == "__main__":
    main()
