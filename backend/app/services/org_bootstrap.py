"""Work done when a new organization is created (signup)."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
SYNC_LIMIT = 150  # most urgent lines handled inline; the rest in a background job


def bootstrap_org_job(org_id: int, line_ids: list[int] | None = None) -> None:
    from app.db import session_scope
    from app.services.recommendations import refresh_org_recommendations

    with session_scope() as db:
        refresh_org_recommendations(db, org_id, auto_quotes=True, line_ids=line_ids)


def refresh_org_soon(db: Session, org_id: int) -> int:
    """Recompute an org's recommendations: the SYNC_LIMIT most urgent lines now, the rest in a background job.

    A live org watches thousands of open lines; doing all of them inside a request takes over a minute.
    Returns how many lines were queued for later.
    """
    from app.models import OrgSettings
    from app.services.recommendations import open_lines, refresh_org_recommendations

    s = db.get(OrgSettings, org_id)
    lines = open_lines(db, fscs=list(s.fsc_watchlist or [])) if s else []
    lines.sort(key=lambda ln: ln.solicitation.return_by_date or date.max)
    ids = [ln.id for ln in lines]
    refresh_org_recommendations(db, org_id, auto_quotes=True, line_ids=ids[:SYNC_LIMIT])
    db.commit()
    rest = ids[SYNC_LIMIT:]
    if rest:
        from app.jobs.queue import enqueue

        enqueue(bootstrap_org_job, org_id, rest)
    return len(rest)


def on_organization_created(db: Session, org_id: int) -> None:
    """Give a new organization recommendations for today's open lines right away.

    The most urgent lines run inline so the dashboard is populated on first load; any remainder is
    queued. Failures here must never block signup.
    """
    try:
        refresh_org_soon(db, org_id)
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        log.exception("Bootstrapping recommendations for org %s failed", org_id)
