"""APScheduler process: enqueues the recurring jobs. `python -m app.jobs.scheduler`.

Schedules (America/New_York):
  * 06:00 daily      - DIBBS RFQ ingest
  * 07:30 daily      - award labeling (newly posted awards)
  * every hour (:15) - email follow-ups / no_response marking
  * Sunday 03:00     - retrain, evaluate, promote if better
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.jobs import tasks
from app.jobs.queue import enqueue

TZ = "America/New_York"

SCHEDULE = [
    ("daily_ingest", tasks.daily_ingest_job, CronTrigger(hour=6, minute=0, timezone=TZ)),
    ("award_labeling", tasks.award_labeling_job, CronTrigger(hour=7, minute=30, timezone=TZ)),
    ("followups", tasks.followups_job, CronTrigger(minute=15, timezone=TZ)),
    ("retrain", tasks.retrain_job, CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=TZ)),
]


def build_scheduler() -> BlockingScheduler:
    sched = BlockingScheduler(timezone=TZ)
    for job_id, func, trigger in SCHEDULE:
        sched.add_job(enqueue, trigger, args=[func], id=job_id, replace_existing=True, misfire_grace_time=3600)
    return sched


def main() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sched = build_scheduler()
    for job in sched.get_jobs():
        logging.info("scheduled %s: %s", job.id, job.trigger)
    sched.start()


if __name__ == "__main__":
    main()
