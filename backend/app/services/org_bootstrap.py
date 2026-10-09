"""Work done when a new organization is created (signup)."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
SYNC_LIMIT = 150  # above this many open lines, bootstrap in the background


def bootstrap_org_job(org_id: int) -> None:
    from app.db import session_scope
    from app.services.recommendations import refresh_org_recommendations

    with session_scope() as db:
        refresh_org_recommendations(db, org_id, create_quote_drafts=True)


def on_organization_created(db: Session, org_id: int) -> None:
    """Give a new organization recommendations for today's open lines right away.

    Small workloads run inline (the dashboard is ready on first load); large ones are queued.
    Failures here must never block signup.
    """
    try:
        from app.models import OrgSettings
        from app.services.recommendations import open_lines, refresh_org_recommendations

        s = db.get(OrgSettings, org_id)
        n = len(open_lines(db, fscs=list(s.fsc_watchlist or []))) if s else 0
        if n > SYNC_LIMIT:
            from app.jobs.queue import enqueue

            enqueue(bootstrap_org_job, org_id)
            return
        refresh_org_recommendations(db, org_id, create_quote_drafts=True)
        db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        log.exception("Bootstrapping recommendations for org %s failed", org_id)
