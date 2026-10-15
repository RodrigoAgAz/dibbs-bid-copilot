"""Award histories (quality-filtered, as in training) and the similar-items index, for live pricing."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovedSource, AwardHistory, ItemCharacteristic, SolicitationLine
from app.services.data_quality import filter_awards
from dibbs_ml.features import PriorAwards
from dibbs_ml.items import Item, ItemIndex, modifier_tokens, noun, pn_family

INDEX_TTL_SECONDS = 6 * 3600
_lock = threading.Lock()
_state: dict = {"index": None, "built": 0.0}


def load_histories(db: Session, nsns: Iterable[str]) -> tuple[dict[str, PriorAwards], dict[str, int]]:
    """Award history per NSN after the same quality filter training uses; plus raw row counts."""
    nsns = sorted(set(nsns))
    if not nsns:
        return {}, {}
    rows = db.execute(
        select(AwardHistory.nsn, AwardHistory.award_date, AwardHistory.unit_price, AwardHistory.quantity,
               AwardHistory.awardee_cage, AwardHistory.source)
        .where(AwardHistory.nsn.in_(nsns), AwardHistory.unit_price > 0, AwardHistory.quantity > 0)
    ).all()
    if not rows:
        return {}, {}
    df = pd.DataFrame(rows, columns=["nsn", "award_date", "unit_price", "quantity", "awardee_cage", "source"])
    raw = df.groupby("nsn").size().to_dict()
    kept, _ = filter_awards(df)
    return {n: PriorAwards.from_frame(g) for n, g in kept.groupby("nsn")}, {k: int(v) for k, v in raw.items()}


def build_item_index(db: Session) -> ItemIndex:
    lines = pd.DataFrame(
        db.execute(select(SolicitationLine.nsn, SolicitationLine.nomenclature, SolicitationLine.unit_of_issue)).all(),
        columns=["nsn", "nomenclature", "unit_of_issue"],
    )
    sources = pd.DataFrame(
        db.execute(select(ApprovedSource.nsn, ApprovedSource.cage_code, ApprovedSource.part_number)).all(),
        columns=["nsn", "cage_code", "part_number"],
    )
    # FLIS / PUB LOG characteristics, when imported (app/pipelines/enrichment.py).
    chars = pd.DataFrame(
        db.execute(select(ItemCharacteristic.nsn, ItemCharacteristic.mrc, ItemCharacteristic.value)).all(),
        columns=["nsn", "mrc", "value"],
    )
    return ItemIndex.from_frames(lines, sources, characteristics=chars if len(chars) else None)


def item_index(db: Session) -> ItemIndex:
    """Process-wide cached index (rebuilt every INDEX_TTL_SECONDS)."""
    with _lock:
        if _state["index"] is None or time.monotonic() - _state["built"] > INDEX_TTL_SECONDS:
            _state["index"] = build_item_index(db)
            _state["built"] = time.monotonic()
        return _state["index"]


def clear_item_index() -> None:
    with _lock:
        _state["index"] = None


def ensure_item(db: Session, index: ItemIndex, line: SolicitationLine) -> None:
    """A line ingested after the cached index was built: describe it on the fly."""
    if line.nsn in index.items or not line.nomenclature or len(line.nsn) != 13:
        return
    srcs = db.execute(select(ApprovedSource.cage_code, ApprovedSource.part_number)
                      .where(ApprovedSource.nsn == line.nsn)).all()
    cages = frozenset(str(c).upper() for c, _ in srcs if c)
    fams = frozenset(f for f in (pn_family(str(c).upper(), p) for c, p in srcs if c) if f)
    it = Item(nsn=line.nsn, fsc=line.nsn[:4], noun=noun(line.nomenclature),
              mods=frozenset(modifier_tokens(line.nomenclature)), unit=(line.unit_of_issue or "").upper(),
              cages=cages, families=fams)
    with _lock:
        index.items[line.nsn] = it
        index.by_group.setdefault((it.fsc, it.noun), []).append(line.nsn)
        index.by_fsc.setdefault(it.fsc, []).append(line.nsn)
        for c in cages:
            index.by_cage.setdefault(f"{it.fsc}:{c}", []).append(line.nsn)
        for f in fams:
            index.by_family.setdefault(f, []).append(line.nsn)
