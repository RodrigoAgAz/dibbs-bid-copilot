"""Ingest, award labeling, backfill and the FSC report in fixture mode (real captured DIBBS data)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import func, select

from app.models import (
    ApprovedSource,
    AwardHistory,
    IngestCheckpoint,
    IngestionRun,
    Organization,
    OrgSettings,
    Recommendation,
    Solicitation,
    SolicitationLine,
)
from app.pipelines.awards import run_award_labeling
from app.pipelines.backfill import run_backfill
from app.pipelines.fsc_report import write_fsc_volume_report
from app.pipelines.ingest import run_daily_ingest


def _org(db, fscs):
    org = Organization(name="Pipeline Org")
    db.add(org)
    db.flush()
    db.add(OrgSettings(organization_id=org.id, fsc_watchlist=fscs))
    db.commit()
    return org


def count(db, model, *where):
    return db.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def test_fixture_ingest_is_idempotent_and_recommends(db, monkeypatch):
    # Pretend "today" is the capture date so the fixture RFQs are open.
    from datetime import date

    monkeypatch.setattr("app.pipelines.ingest.today_et", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.recommendations.today_et", lambda: date(2026, 10, 9))
    org = _org(db, ["6145", "5330"])
    s1 = run_daily_ingest(db, mode="fixture")
    assert s1["status"] == "success" and s1["errors"] == 0
    assert s1["matched_lines"] == 107 + 384
    n_lines = count(db, SolicitationLine)
    assert n_lines > 0
    # Details from the captured record pages/PDFs (T0501 = 5330 gasket; T0003 = 6145 wire).
    t0501 = db.execute(select(Solicitation).where(Solicitation.solicitation_number == "SPE7L127T0501")).scalar_one()
    assert t0501.buyer_email == "kelly.day@dla.mil" and t0501.naics == "339991"
    assert count(db, ApprovedSource, ApprovedSource.nsn == "5330016630871") >= 1
    assert count(db, AwardHistory, AwardHistory.nsn == "5330016630871") == 2
    assert count(db, Recommendation, Recommendation.organization_id == org.id) > 0
    run = db.execute(select(IngestionRun).order_by(IngestionRun.id.desc())).scalars().first()
    assert run.status == "success" and run.rows_ingested == s1["new_lines"]
    # Re-run: every date is checkpointed, nothing new.
    s2 = run_daily_ingest(db, mode="fixture")
    assert s2["new_lines"] == 0 and count(db, SolicitationLine) == n_lines
    assert count(db, IngestCheckpoint, IngestCheckpoint.source == "rfq_index") == 5


def test_award_labeling_from_record_page(db, monkeypatch):
    from datetime import date

    monkeypatch.setattr("app.pipelines.ingest.today_et", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.pipelines.awards.today_et", lambda: date(2026, 10, 9))
    _org(db, ["6145"])
    run_daily_ingest(db, mode="fixture", make_recommendations=False)
    res = run_award_labeling(db, mode="fixture")
    assert res["status"] in ("success", "partial")
    award = db.execute(
        select(AwardHistory).where(AwardHistory.solicitation_number == "SPE7M027T0003")
    ).scalars().first()
    assert award is not None
    assert award.awardee_cage == "1V630" and award.contract_number == "SPE7M027P0185"
    assert abs(award.unit_price - 240.58 / 493) < 1e-4
    assert res["post_dates"]  # award listing dates processed and checkpointed


def test_backfill_resumable_and_report(db):
    s1 = run_backfill(db, ["6145"], date_(2023, 1, 1), date_(2026, 10, 9), mode="fixture")
    assert s1["index_dates"] == 5 and s1["status"] in ("success", "partial")
    n_awards = count(db, AwardHistory)
    s2 = run_backfill(db, ["6145"], date_(2023, 1, 1), date_(2026, 10, 9), mode="fixture")
    assert s2["index_dates"] == 0 and s2["nsn_pdfs"] == 0  # resumed: nothing redone
    assert count(db, AwardHistory) == n_awards
    out = Path(tempfile.mkdtemp())
    try:
        path = write_fsc_volume_report(db, out / "fsc_volume.md")
        text = path.read_text(encoding="utf-8")
        assert "| 6145 *" in text and "Automated (T/U)" in text
    finally:
        shutil.rmtree(out, ignore_errors=True)


def date_(y, m, d):
    from datetime import date

    return date(y, m, d)
