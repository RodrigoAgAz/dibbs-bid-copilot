"""APScheduler process: enqueues the recurring jobs. `python -m app.jobs.scheduler`.

Schedules (America/New_York); [bulk] jobs run on the separate bulk queue/worker process:
  * every 2 h, 06-20 - DIBBS RFQ ingest (DIBBS posts each day's files the next day, at varying times;
                       checkpoints make repeat runs cheap: only new files are processed)
  * 01:00 daily      - [bulk] nightly incremental backfill (more price history; live mode only)
  * 07:30 daily      - [bulk] award labeling (newly posted awards, closed tracked solicitations)
  * every hour (:15) - email follow-ups / no_response marking
  * every hour (:45) - data-health check (logs; emails OPS_ALERT_EMAIL while degraded)
  * Sunday 03:00     - [bulk] retrain, evaluate, promote if better
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.jobs import tasks
from app.jobs.queue import enqueue_unique

TZ = "America/New_York"

SCHEDULE = [
    ("daily_ingest", tasks.daily_ingest_job, CronTrigger(hour="6-20/2", minute=0, timezone=TZ)),
    ("nightly_backfill", tasks.nightly_backfill_job, CronTrigger(hour=1, minute=0, timezone=TZ)),
    ("award_labeling", tasks.award_labeling_job, CronTrigger(hour=7, minute=30, timezone=TZ)),
    ("followups", tasks.followups_job, CronTrigger(minute=15, timezone=TZ)),
    ("ops_check", tasks.ops_check_job, CronTrigger(minute=45, timezone=TZ)),
    ("retrain", tasks.retrain_job, CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=TZ)),
]


def build_scheduler() -> BlockingScheduler:
    sched = BlockingScheduler(timezone=TZ)
    for job_id, func, trigger in SCHEDULE:
        sched.add_job(enqueue_unique, trigger, args=[func], id=job_id, replace_existing=True, misfire_grace_time=3600)
    return sched


def main() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sched = build_scheduler()
    for job in sched.get_jobs():
        logging.info("scheduled %s: %s", job.id, job.trigger)
    sched.start()


if __name__ == "__main__":
    main()
