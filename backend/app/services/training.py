"""Train from the database, register the model run, write the report, promote if better."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApprovedSource, AwardHistory, ModelRun, Solicitation, SolicitationLine
from app.services.pricing import clear_model_cache, get_active_model
from dibbs_ml.report import write_model_report
from dibbs_ml.train import save_artifact, should_promote, train_from_awards

log = logging.getLogger(__name__)


REAL_ONLY_THRESHOLD = 500


def load_awards_frame(db: Session) -> pd.DataFrame:
    """award_history joined to solicitation context (by solicitation number + NSN) where available.

    Once there are >= REAL_ONLY_THRESHOLD real (non-synthetic) award rows, synthetic demo rows are excluded,
    so a live deployment that started from the demo seed trains on real DIBBS data only.
    """
    n_real = db.execute(
        select(func.count()).select_from(AwardHistory).where(AwardHistory.source != "synthetic")
    ).scalar_one()
    real_only = n_real >= REAL_ONLY_THRESHOLD
    stmt = select(
        AwardHistory.nsn,
        AwardHistory.award_date,
        AwardHistory.unit_price,
        AwardHistory.quantity,
        AwardHistory.unit_of_issue,
        AwardHistory.awardee_cage,
        AwardHistory.contract_number,
        AwardHistory.solicitation_number,
    )
    if real_only:
        stmt = stmt.where(AwardHistory.source != "synthetic")
    awards = pd.DataFrame(
        db.execute(stmt).all(),
        columns=["nsn", "award_date", "unit_price", "quantity", "unit_of_issue", "awardee_cage",
                 "contract_number", "solicitation_number"],
    )
    if awards.empty:
        return awards
    awards["fsc"] = awards["nsn"].str[:4]
    ctx = pd.DataFrame(
        db.execute(
            select(
                Solicitation.solicitation_number,
                SolicitationLine.nsn,
                Solicitation.issue_date,
                Solicitation.type_code,
                Solicitation.buyer_code,
                Solicitation.set_aside,
                Solicitation.inspection_point,
                Solicitation.fob,
                Solicitation.first_article,
                Solicitation.fast_award,
                Solicitation.aidc,
                SolicitationLine.amsc,
                SolicitationLine.approved_source_only,
                SolicitationLine.dod_standard_price,
            ).join(SolicitationLine, SolicitationLine.solicitation_id == Solicitation.id)
        ).all(),
        columns=["solicitation_number", "nsn", "issue_date", "type_code", "buyer_code", "set_aside",
                 "inspection_point", "fob", "first_article", "fast_award", "aidc", "amsc",
                 "approved_source_only", "dod_standard_price"],
    ).drop_duplicates(subset=["solicitation_number", "nsn"])
    df = awards.merge(ctx, on=["solicitation_number", "nsn"], how="left")
    n_src = pd.DataFrame(
        db.execute(select(ApprovedSource.nsn, func.count()).group_by(ApprovedSource.nsn)).all(),
        columns=["nsn", "n_approved_sources"],
    )
    df = df.merge(n_src, on="nsn", how="left")
    return df


def train_and_maybe_promote(db: Session, as_of: date, force_promote: bool = False) -> dict:
    df = load_awards_frame(db)
    active_run, active_model = get_active_model(db)
    result = train_from_awards(df, as_of=as_of, active_model=active_model)
    promote, reason = should_promote(result.metrics)
    if force_promote:
        promote, reason = True, f"forced ({reason})"
    run = ModelRun(
        model_type="lgbm_quantile_conformal",
        train_window=result.train_window,
        test_window=result.test_window,
        metrics=result.metrics,
        is_active=False,
        notes=reason,
    )
    db.add(run)
    db.flush()
    path = save_artifact(result.model, Path(get_settings().model_artifact_dir), run.id)
    run.artifact_path = str(path)
    run.artifact_blob = Path(path).read_bytes()
    if promote:
        db.execute(update(ModelRun).where(ModelRun.id != run.id).values(is_active=False))
        run.is_active = True
    run.metrics = {**result.metrics, "promoted": promote, "promote_reason": reason}
    db.flush()
    n_synth = db.execute(
        select(func.count()).select_from(AwardHistory).where(AwardHistory.source == "synthetic")
    ).scalar_one()
    note = (
        f"_Trained on {len(df):,} award rows, of which {n_synth:,} are the synthetic demo history from "
        "`make seed`. Metrics on synthetic data show the pipeline works; they are not evidence about real DIBBS prices._"
        if n_synth
        else f"_Trained on {len(df):,} real award rows from the database._"
    )
    report = write_model_report(run.metrics, run.id, promote, reason, data_note=note)
    clear_model_cache()
    if promote:
        _repredict_open_lines(db)
    log.info("model run %s: promoted=%s (%s)", run.id, promote, reason)
    return {
        "model_run_id": run.id,
        "promoted": promote,
        "reason": reason,
        "pinball_mean": result.metrics["pinball_mean"],
        "coverage_80": result.metrics["model"]["coverage_80"],
        "coverage_90": result.metrics["model"]["coverage_90"],
        "report": str(report),
        "previous_active": active_run.id if active_run else None,
    }


def _repredict_open_lines(db: Session) -> None:
    from app.core.timeutil import today_et
    from app.models import Organization
    from app.services.pricing import fsc_median_index, predict_line
    from app.services.recommendations import open_lines, refresh_org_recommendations

    lines = open_lines(db)
    if not lines:
        return
    idx = fsc_median_index(db, {ln.fsc for ln in lines}, today_et())
    for ln in lines:
        predict_line(db, ln, fsc_index=idx)
    db.flush()
    for org_id in db.execute(select(Organization.id)).scalars():
        refresh_org_recommendations(db, org_id)
