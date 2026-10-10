"""Deployment concerns: hosted Postgres URLs, model artifacts shared through the database."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app.config import Settings
from app.core.timeutil import today_et
from app.models import ModelRun
from app.services import pricing
from app.services.training import train_and_maybe_promote
from tests.helpers import build_world


def test_hosted_postgres_urls_use_psycopg_driver():
    for raw in ("postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"):
        assert Settings(database_url=raw).database_url == "postgresql+psycopg://u:p@h:5432/db"
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"


def test_model_loads_from_database_when_file_is_missing(db):
    build_world(db, n_nsns=80, n_open=4, seed=9)
    res = train_and_maybe_promote(db, as_of=today_et() - timedelta(days=1), force_promote=True)
    db.commit()
    run = db.get(ModelRun, res["model_run_id"])
    Path(run.artifact_path).unlink()  # e.g. trained by the worker on another container
    pricing.clear_model_cache()
    loaded_run, model = pricing.get_active_model(db)
    assert loaded_run.id == run.id and model is not None


def test_training_ignores_synthetic_rows_once_real_history_exists(db):
    from datetime import date

    from app.models import AwardHistory
    from app.services.training import REAL_ONLY_THRESHOLD, load_awards_frame

    def rows(source, n, prefix):
        return [AwardHistory(nsn=f"530501{prefix}{i:05d}", award_date=date(2025, 1, 1), contract_number=f"C{prefix}{i}",
                             quantity=1, unit_price=1.0, source=source) for i in range(n)]

    db.add_all(rows("synthetic", 50, "9") + rows("rfq_pdf_history", REAL_ONLY_THRESHOLD - 1, "1"))
    db.commit()
    assert set(load_awards_frame(db)["contract_number"].str[1]) == {"9", "1"}  # not enough real data yet
    db.add_all(rows("rfq_pdf_history", 1, "2"))
    db.commit()
    assert "9" not in set(load_awards_frame(db)["contract_number"].str[1])
