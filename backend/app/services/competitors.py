"""Competitor index per supply class (public award data, cached process-wide) for contestability and the
competitor view of P(win). See dibbs_ml.competitors for the maths."""

from __future__ import annotations

import threading
import time

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import today_et
from app.models import ApprovedSource, AwardHistory, Company
from app.services.data_quality import filter_awards
from dibbs_ml.competitors import FscCompetitors
from dibbs_ml.inflation import default_ppi

INDEX_TTL_SECONDS = 6 * 3600
_lock = threading.Lock()
_state: dict = {"by_fsc": {}, "oem": None, "oem_built": 0.0}


def manufacturer_cages(db: Session) -> set[str]:
    """CAGEs DLA treats as manufacturers: approved sources for some NSN, or named as the manufacturer on an RFQ."""
    with _lock:
        if _state["oem"] is not None and time.monotonic() - _state["oem_built"] < INDEX_TTL_SECONDS:
            return _state["oem"]
    oem = {str(c).upper() for (c,) in db.execute(select(ApprovedSource.cage_code).distinct()).all() if c}
    oem |= {str(c).upper() for (c,) in db.execute(select(Company.cage_code).where(Company.is_manufacturer_guess.is_(True))).all() if c}
    with _lock:
        _state["oem"], _state["oem_built"] = oem, time.monotonic()
    return oem


def load_fsc_awards(db: Session, fsc: str) -> pd.DataFrame:
    rows = db.execute(
        select(AwardHistory.nsn, AwardHistory.award_date, AwardHistory.awardee_cage, AwardHistory.unit_price,
               AwardHistory.quantity, AwardHistory.source, AwardHistory.solicitation_number)
        .where(AwardHistory.nsn.like(f"{fsc}%"), AwardHistory.awardee_cage.is_not(None), AwardHistory.unit_price > 0)
    ).all()
    df = pd.DataFrame(rows, columns=["nsn", "award_date", "awardee_cage", "unit_price", "quantity", "source",
                                     "solicitation_number"])
    if df.empty:
        return df
    kept, _ = filter_awards(df)  # the same verified-price rule as training
    return kept.drop(columns=["source"])


def build_fsc_competitors(db: Session, fsc: str) -> FscCompetitors:
    df = load_fsc_awards(db, fsc)
    cages = set(df["awardee_cage"].astype(str).str.upper()) if not df.empty else set()
    names = dict(db.execute(select(Company.cage_code, Company.name).where(Company.cage_code.in_(cages))).all()) if cages else {}
    return FscCompetitors.build(fsc, df, manufacturer_cages(db), ppi=default_ppi(), as_of=today_et(),
                                names={str(k).upper(): v for k, v in names.items() if v})


def fsc_competitors(db: Session, fsc: str) -> FscCompetitors:
    """Cached per supply class (public data: the same for every organization)."""
    with _lock:
        hit = _state["by_fsc"].get(fsc)
        if hit and time.monotonic() - hit[1] < INDEX_TTL_SECONDS:
            return hit[0]
    built = build_fsc_competitors(db, fsc)
    with _lock:
        _state["by_fsc"][fsc] = (built, time.monotonic())
    return built


def clear_competitor_index() -> None:
    with _lock:
        _state["by_fsc"].clear()
        _state["oem"] = None
