"""Public, unauthenticated marketing data: today's top lines from the public demo.

Only served when PUBLIC_DEMO=true, and only what the read-only demo already shows any visitor: real DIBBS
solicitations as priced for the fictional demo broker. No customer data is ever read here.
"""

from __future__ import annotations

import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.timeutil import today_et
from app.db import get_db
from app.models import Organization, Recommendation, Solicitation, SolicitationLine
from app.services.accounts import DEMO_VIEWER_ORG
from app.services.biddable import not_awarded

router = APIRouter(prefix="/api/public", tags=["public"])

_cache: dict[str, tuple[float, list]] = {}
CACHE_SECONDS = 600


class TopLine(BaseModel):
    solicitation_number: str
    nomenclature: str | None
    nsn: str
    quantity: float
    unit_of_issue: str | None
    return_by_date: date | None
    predicted_median: float | None
    range_low: float | None
    range_high: float | None
    n_prior_awards: int | None
    recommended_bid: float | None
    p_win: float | None


@router.get("/top-lines", response_model=list[TopLine])
def top_lines(db: Session = Depends(get_db)) -> list[TopLine]:
    if not get_settings().public_demo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not available")
    hit = _cache.get("top")
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    org = db.execute(select(Organization).where(Organization.name == DEMO_VIEWER_ORG)).scalar_one_or_none()
    if org is None:
        return []
    today = today_et()
    rows = db.execute(
        select(Recommendation, SolicitationLine, Solicitation)
        .join(SolicitationLine, Recommendation.solicitation_line_id == SolicitationLine.id)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .where(
            Recommendation.organization_id == org.id,
            Recommendation.no_bid.is_(False),
            Recommendation.recommended_bid.is_not(None),
            Solicitation.return_by_date >= today,
            not_awarded(),
            ~Solicitation.solicitation_number.startswith("SPEZZ"),
        )
    ).all()
    good = [r for r in rows if (r[0].explanation or {}).get("confidence") == "high"]
    good.sort(key=lambda r: -((r[0].explanation or {}).get("rank_score") or 0))
    out = []
    for rec, ln, sol in good[:3]:
        ex = rec.explanation or {}
        lo, hi = (ex.get("price_range") or [None, None])[:2]
        out.append(TopLine(
            solicitation_number=sol.solicitation_number, nomenclature=ln.nomenclature, nsn=ln.nsn,
            quantity=ln.quantity, unit_of_issue=ln.unit_of_issue, return_by_date=sol.return_by_date,
            predicted_median=(ex.get("distribution") or {}).get("median"), range_low=lo, range_high=hi,
            n_prior_awards=ex.get("n_prior_awards"), recommended_bid=rec.recommended_bid,
            p_win=rec.p_win_at_recommended,
        ))
    _cache["top"] = (time.monotonic(), out)
    return out
