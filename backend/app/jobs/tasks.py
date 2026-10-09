"""Background job functions (run by the RQ worker, or inline when Redis is unavailable)."""

from __future__ import annotations

import logging
from datetime import date

from app.db import session_scope

log = logging.getLogger(__name__)


def daily_ingest_job(mode: str | None = None) -> dict:
    from app.pipelines.ingest import run_daily_ingest

    with session_scope() as db:
        return run_daily_ingest(db, mode=mode)


def award_labeling_job(mode: str | None = None) -> dict:
    from app.pipelines.awards import run_award_labeling

    with session_scope() as db:
        return run_award_labeling(db, mode=mode)


def retrain_job() -> dict:
    from app.services.training import train_and_maybe_promote

    with session_scope() as db:
        return train_and_maybe_promote(db, as_of=date.today())


def followups_job() -> dict:
    from app.services.sourcing import run_followups

    with session_scope() as db:
        return run_followups(db)


def send_quote_request_job(quote_request_id: int) -> dict:
    from app.services.sourcing import send_quote_request_by_id

    with session_scope() as db:
        return send_quote_request_by_id(db, quote_request_id)
