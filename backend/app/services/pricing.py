"""Model inference: predicted distribution of the winning unit price for a solicitation line."""

from __future__ import annotations

import logging
import math
import threading
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApprovedSource, AwardHistory, ModelRun, PricePrediction, Solicitation, SolicitationLine
from dibbs_ml.cdf import QUANTILE_LEVELS, PriceDistribution
from dibbs_ml.features import FscMedianIndex, PriorAwards, cold_reference, compute_features, similar_awards
from dibbs_ml.inflation import default_ppi
from dibbs_ml.models import LOW_DATA_PRIOR_AWARDS, PricingModel

log = logging.getLogger(__name__)
_lock = threading.Lock()
_cache: dict[str, tuple[int, PricingModel]] = {}

HEURISTIC_SIGMA_WARM = 0.25
HEURISTIC_SIGMA_COLD = 0.55


def get_active_model(db: Session) -> tuple[ModelRun | None, PricingModel | None]:
    run = db.execute(
        select(ModelRun).where(ModelRun.is_active.is_(True)).order_by(ModelRun.created_at.desc())
    ).scalars().first()
    if run is None:
        return None, None
    with _lock:
        cached = _cache.get("active")
        if cached and cached[0] == run.id:
            return run, cached[1]
        path = Path(run.artifact_path) if run.artifact_path else None
        try:
            if path is None or not path.exists():
                blob = db.execute(select(ModelRun.artifact_blob).where(ModelRun.id == run.id)).scalar_one_or_none()
                if not blob:
                    return run, None
                # Materialize the stored artifact locally (another service trained it).
                path = Path(get_settings().model_artifact_dir) / f"model_run_{run.id}.joblib"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)
            model = PricingModel.load(path)
        except Exception:
            log.exception("could not load model artifact for run %s", run.id)
            return run, None
        _cache["active"] = (run.id, model)
        return run, model


def clear_model_cache() -> None:
    with _lock:
        _cache.clear()


def line_context(line: SolicitationLine, sol: Solicitation, n_sources: int) -> dict:
    return {
        "nsn": line.nsn,
        "fsc": line.fsc,
        "unit_of_issue": line.unit_of_issue,
        "buyer_code": sol.buyer_code,
        "amsc": line.amsc,
        "type_code": sol.type_code,
        "set_aside": sol.set_aside,
        "inspection_point": sol.inspection_point,
        "fob": sol.fob,
        "approved_source_only": line.approved_source_only,
        "n_approved_sources": n_sources,
        "fast_award": sol.fast_award,
        "aidc": sol.aidc,
        "first_article": sol.first_article,
        "dod_standard_price": line.dod_standard_price,
    }


def nsn_history(db: Session, nsn: str) -> PriorAwards:
    """The NSN's award history after the training quality filter (services/data_quality.py)."""
    from app.services.item_index import load_histories

    hists, _ = load_histories(db, [nsn])
    return hists.get(nsn, PriorAwards.empty())


def fsc_median_index(db: Session, fscs: set[str], as_of: date) -> FscMedianIndex:
    since = as_of - timedelta(days=40 * 31)
    rows = db.execute(
        select(func.substr(AwardHistory.nsn, 1, 4), AwardHistory.award_date, AwardHistory.unit_price).where(
            func.substr(AwardHistory.nsn, 1, 4).in_(fscs), AwardHistory.award_date >= since, AwardHistory.unit_price > 0
        )
    ).all()
    return FscMedianIndex(pd.DataFrame(rows, columns=["fsc", "award_date", "unit_price"]))


def _heuristic_logq(ref: float, cold: bool) -> np.ndarray:
    from scipy.stats import norm

    sigma = HEURISTIC_SIGMA_COLD if cold else HEURISTIC_SIGMA_WARM
    return math.log(ref) + sigma * norm.ppf(np.array(QUANTILE_LEVELS))


def predict_line(
    db: Session,
    line: SolicitationLine,
    as_of: date | None = None,
    fsc_index: FscMedianIndex | None = None,
    commit_row: bool = True,
) -> PricePrediction:
    as_of = as_of or date.today()
    sol = line.solicitation
    run, model = get_active_model(db)
    n_sources = db.execute(select(func.count()).select_from(ApprovedSource).where(ApprovedSource.nsn == line.nsn)).scalar_one()
    ctx = line_context(line, sol, int(n_sources))
    from app.services.item_index import ensure_item, item_index, load_histories
    from dibbs_ml.items import similar_item_features

    hists, raw_counts = load_histories(db, [line.nsn])
    hist = hists.get(line.nsn, PriorAwards.empty())
    fsc_index = fsc_index or fsc_median_index(db, {line.fsc}, as_of)
    fmed = fsc_index.get(line.fsc, as_of)
    ppi = default_ppi()
    feats, ref, ref_type = compute_features(hist, ctx, line.quantity, as_of, ppi, fmed)
    cold_ref, cold_ref_type = cold_reference(ctx, fmed)
    # Similar items (same part-number family / manufacturer): features for the model + evidence for the broker.
    sim_ref, evidence = None, []
    try:
        items = item_index(db)
        ensure_item(db, items, line)
        nbrs = items.neighbors(line.nsn)
        if nbrs:
            nh, _ = load_histories(db, [n for n, _, _ in nbrs])
            sim, sim_ref, evidence = similar_item_features(nbrs, nh, line.quantity, as_of, ppi, line.fsc, fmed,
                                                           items=items, nsn=line.nsn)
            feats.update(sim)
    except Exception:  # similar items must never block a prediction
        log.exception("similar items failed for line %s", line.id)
    n_prior = int(feats["n_prior_awards"])
    is_cold = n_prior == 0
    row = pd.DataFrame(
        [{**feats, "is_cold": is_cold, "ref_price": ref, "cold_ref_price": cold_ref, "cold_ref_type": cold_ref_type,
          "sim_ref_price": sim_ref if sim_ref else float("nan")}]
    )
    contributions: list[dict] = []
    if model is not None and not (is_cold and cold_ref_type == "none"):
        logq, _ = model.predict_log(row)
        logq = logq[0]
        model_name = model.model_version
        try:
            contributions = model.explain(row)
        except Exception:  # explanations must never block a prediction
            log.exception("explain failed for line %s", line.id)
    else:
        base = (sim_ref or cold_ref) if is_cold else ref
        logq = _heuristic_logq(base, is_cold)
        model_name = "heuristic_v0"
    dist = PriceDistribution(QUANTILE_LEVELS, np.exp(logq))
    uses_similar = bool(sim_ref) and (model is None or getattr(model, "cold_ref_mode", "fsc") == "similar")
    cold_used, cold_used_type = (sim_ref, "similar_items") if uses_similar else (cold_ref, cold_ref_type)
    payload = {
        **dist.to_dict(),
        "median": round(dist.median, 4),
        "low_data": n_prior < LOW_DATA_PRIOR_AWARDS,
        "cold_start": is_cold,
        "n_prior_awards": n_prior,
        # Award rows on file when priced (raw, before the quality filter): more later = stale (refresh_stale_predictions).
        "history_rows": int(raw_counts.get(line.nsn, 0)),
        "similar_items": evidence,
        "reference_price": round(cold_used if is_cold else ref, 4),
        "reference_type": cold_used_type if is_cold else ref_type,
        "model": model_name,
        "as_of": as_of.isoformat(),
        "contributions": contributions,
        "similar_awards": similar_awards(hist, line.quantity, as_of + timedelta(days=1)),
    }
    pred = PricePrediction(solicitation_line_id=line.id, model_run_id=run.id if (run and model) else None, quantiles=payload)
    db.add(pred)
    if commit_row:
        db.flush()
    return pred


def latest_prediction(db: Session, line_id: int) -> PricePrediction | None:
    return db.execute(
        select(PricePrediction)
        .where(PricePrediction.solicitation_line_id == line_id)
        .order_by(PricePrediction.created_at.desc(), PricePrediction.id.desc())
    ).scalars().first()


def distribution_of(pred: PricePrediction) -> PriceDistribution:
    return PriceDistribution.from_dict(pred.quantiles)
