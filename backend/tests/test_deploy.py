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
