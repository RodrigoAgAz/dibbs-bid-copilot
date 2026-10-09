"""Per-organization recommendations (filled in by the recommendation engine phase)."""

from __future__ import annotations

from sqlalchemy.orm import Session


def refresh_org_recommendations(db: Session, org_id: int, create_quote_drafts: bool = False) -> int:
    return 0
