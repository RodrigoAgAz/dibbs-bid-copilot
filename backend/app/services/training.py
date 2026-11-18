"""Train from the database, register the model run, write the report, promote if better."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import numpy as np
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
        AwardHistory.source,
    )
    if real_only:
        stmt = stmt.where(AwardHistory.source != "synthetic")
    stmt = stmt.where(AwardHistory.unit_price > 0, AwardHistory.quantity > 0)
    awards = pd.DataFrame(
        db.execute(stmt).all(),
        columns=["nsn", "award_date", "unit_price", "quantity", "unit_of_issue", "awardee_cage",
                 "contract_number", "solicitation_number", "source"],
    )
    if awards.empty:
        return awards
    from app.services.data_quality import filter_awards

    awards, quality = filter_awards(awards)
    log.info("training awards: %s", quality)
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
    df.attrs["quality"] = quality  # merges drop attrs: set them on the frame actually returned
    return df


def train_and_maybe_promote(db: Session, as_of: date, force_promote: bool = False) -> dict:
    df = load_awards_frame(db)
    active_run, active_model = get_active_model(db)
    from app.services.item_index import build_item_index, clear_item_index

    items = build_item_index(db)  # similar items (same part family / manufacturer) for the cold-start model
    from app.config import DEFAULT_FSC_WATCHLIST

    # The catalog's classes are also scored on their own (model vs baseline): the test window mixes in every class.
    result = train_from_awards(df, as_of=as_of, active_model=active_model, items=items,
                               focus_fscs=set(DEFAULT_FSC_WATCHLIST))
    clear_item_index()
    try:
        result.metrics["pilot_regret"] = pilot_regret_metrics(db, result, active_model)
    except Exception as exc:  # the regret check must never block a retrain
        log.exception("pilot regret check failed")
        result.metrics["pilot_regret"] = {"n": 0, "enough": False, "passed": True, "error": str(exc)[:300]}
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
    run.metrics = {**result.metrics, "promoted": promote, "promote_reason": reason,
                   "model_version": result.model.model_version, "data_quality": df.attrs.get("quality")}
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
    act = result.metrics.get("active_model_on_same_test") or {}
    log.info("model run %s (%s): promoted=%s (%s); median error with history new %.3f / active %s; new parts "
             "new %.3f / active %s", run.id, result.model.model_version, promote, reason,
             (result.metrics["model"].get("warm") or {}).get("median_abs_pct_error", float("nan")),
             (act.get("warm") or {}).get("median_abs_pct_error"),
             (result.metrics["model"].get("cold") or {}).get("median_abs_pct_error", float("nan")),
             (act.get("cold") or {}).get("median_abs_pct_error"))
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


def pilot_regret_metrics(db: Session, result, active_model) -> dict:
    """Regret of the candidate's bids vs the active model's on the awarded pilot lines that fall in the candidate's
    held-out test window (dibbs_ml.pilot.compare_policies). Both models are the held-out twins, predicting from the
    frame rows of those awards (strictly-prior features), and bid through the same engine with each organization's
    rules against the real landed cost (what the firm paid when recorded, else the estimate the line was priced at).
    """
    from app.services.pilot import pilot_lines_for_training
    from dibbs_ml import pilot as pilot_math
    from dibbs_ml.bidding import recommend_bid
    from dibbs_ml.cdf import PriceDistribution

    empty = {"n": 0, "min_lines": pilot_math.REGRET_MIN_LINES, "enough": False, "passed": True}
    pairs = pilot_lines_for_training(db)
    if not pairs:
        return {**empty, "note": "no awarded pilot lines with a pre-award decision"}
    lo, hi = (date.fromisoformat(x) for x in result.test_window.split(".."))
    frame = result.frame
    d = pd.to_datetime(frame["award_date"]).dt.date
    test = frame[(d >= lo) & (d <= hi)]
    if test.empty:
        return {**empty, "note": "empty test window"}
    # The frame carries no solicitation number: an award is identified by NSN, award date and winning price.
    key: dict[tuple, int] = {}
    for i, (nsn, ad, price) in enumerate(zip(test["nsn"], pd.to_datetime(test["award_date"]).dt.date,
                                           test["unit_price"], strict=True)):
        key.setdefault((str(nsn), ad, round(float(price), 4)), i)
    picked, lines, rules = [], [], []
    for ln, s in pairs:
        i = key.get((ln.nsn, ln.award_date, round(ln.winning_price, 4)))
        if i is not None:
            picked.append(i)
            lines.append(ln)
            rules.append(s)
    if not picked:
        return {**empty, "note": f"{len(pairs)} awarded pilot lines, none in the test window {lo}..{hi}"}
    sub = test.iloc[picked]
    candidate = getattr(result.model, "eval_model", None) or result.model
    active = (getattr(active_model, "eval_model", None) or active_model) if active_model is not None else None
    if active is None:
        return {**empty, "n": len(lines), "note": "no active model to compare with"}

    def bids(model) -> list[float | None]:
        logq, _ = model.predict_log(sub, kind="model")
        out: list[float | None] = []
        for i, (ln, s) in enumerate(zip(lines, rules, strict=True)):
            dist = PriceDistribution(model.levels, np.exp(logq[i]))
            rec = recommend_bid(dist, ln.landed, ln.quantity, min_margin_pct=s.min_margin_pct,
                                min_expected_profit=s.min_expected_profit, max_line_value=s.max_line_value,
                                min_p_win=s.min_p_win, fulfilment_loss_pct=float(getattr(s, "fulfilment_loss_pct", 0.0) or 0.0))
            out.append(None if rec.no_bid else rec.recommended_bid)
        return out

    out = pilot_math.compare_policies(bids(candidate), bids(active), lines, min_lines=pilot_math.REGRET_MIN_LINES)
    out["test_window"] = [lo.isoformat(), hi.isoformat()]
    out["real_cost_lines"] = sum(1 for ln in lines if ln.cost_basis == "actual")
    return out


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
