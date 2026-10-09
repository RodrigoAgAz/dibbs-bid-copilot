"""Small synthetic worlds for API/service tests (no model training unless asked)."""

from __future__ import annotations

from datetime import timedelta

from app.core.timeutil import today_et
from app.models import SolicitationLine
from app.seed.demo import _insert_open_lines, _insert_synthetic
from dibbs_ml.synthetic import generate


def build_world(db, n_nsns: int = 40, n_open: int = 10, seed: int = 5):
    data = generate(n_nsns=n_nsns, end=today_et() - timedelta(days=1), n_open_lines=n_open, seed=seed)
    _insert_synthetic(db, data)
    lines = _insert_open_lines(db, data)
    db.commit()
    return data, [ln.id for ln in lines]


def open_line(db, line_id: int) -> SolicitationLine:
    return db.get(SolicitationLine, line_id)
