"""Work done when a new organization is created (signup)."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def on_organization_created(db: Session, org_id: int) -> None:
    """Give a new organization recommendations for today's open lines right away.

    Failures here must never block signup.
    """
    try:
        from app.services.recommendations import refresh_org_recommendations

        refresh_org_recommendations(db, org_id, create_quote_drafts=True)
        db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        log.exception("Bootstrapping recommendations for org %s failed", org_id)
