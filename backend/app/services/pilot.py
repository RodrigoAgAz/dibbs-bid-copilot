"""Pilot evaluation for one organization: the tool's pre-award decisions against the firm's real bids, costs and outcomes.

Collects every awarded line of the organization with a decision snapshot taken before the award posted, pairs it with
the verified winning price, the firm's bid (recorded in the app or imported) and the landed cost (what the firm paid
when recorded, else the estimate the recommendation was priced against), and hands the lines to `dibbs_ml.pilot`.
Adds the tenant cost model's leave-one-out error and the contestability Brier score on the same tenant, and writes
ml/reports/pilot_<org>.md. Also feeds the model-promotion regret check (services/training.py).

Everything is scoped to one organization by explicit filters: this runs from the CLI and jobs without a bound tenant.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import median

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import today_et
from app.models import (
    AwardHistory,
    Organization,
    OrgSettings,
    Recommendation,
    RecommendationSnapshot,
    Solicitation,
    SolicitationLine,
)
from app.pipelines.pilot_import import HISTORICAL, PILOT_SOURCE
from app.services.competitors import fsc_competitors, load_fsc_awards, manufacturer_cages
from app.services.cost_model import tenant_quote_observations
from app.services.data_quality import MAX_RATIO, RELIABLE_SOURCES
from app.services.recommendations import org_settings
from dibbs_ml.competitors import evaluate_contestability
from dibbs_ml.cost_model import TenantCostModel, summarize_loo
from dibbs_ml.pilot import PilotLine, caveats, render_report, summarize
from dibbs_ml.report import REPORTS

log = logging.getLogger(__name__)

_SOURCE_RANK = {s: 0 for s in RELIABLE_SOURCES} | {PILOT_SOURCE: 1}


def _best_award(rows: list[AwardHistory]) -> AwardHistory:
    return sorted(rows, key=lambda a: (_SOURCE_RANK.get(a.source, 2), a.award_date, a.id))[0]


def collect_pilot_lines(db: Session, org_id: int) -> tuple[list[PilotLine], dict]:
    """Every awarded line of the organization that the evaluation can score, plus counts of what was left out."""
    org = db.get(Organization, org_id)
    cage = (org.cage_code or "").strip().upper() if org else ""
    rows = db.execute(
        select(Recommendation, SolicitationLine, Solicitation)
        .join(SolicitationLine, Recommendation.solicitation_line_id == SolicitationLine.id)
        .join(Solicitation, SolicitationLine.solicitation_id == Solicitation.id)
        .where(Recommendation.organization_id == org_id)
    ).all()
    inputs = {"recommendations": len(rows), "historical_rows": sum(1 for r, _, _ in rows if r.source == HISTORICAL),
              "firm_bids": sum(1 for r, _, _ in rows if r.actual_bid is not None),
              "with_actual_cost": sum(1 for r, _, _ in rows if r.actual_cost_per_unit is not None),
              "awarded": 0, "verified_price": 0, "outcomes_from_firm": 0, "with_pre_award_decision": 0,
              "decisions_from_updated_at": 0, "skipped_no_cost_basis": 0}
    if not rows:
        return [], inputs
    sols = {sol.solicitation_number for _, _, sol in rows}
    by_key: dict[tuple[str, str], list[AwardHistory]] = {}
    for a in db.execute(select(AwardHistory).where(AwardHistory.solicitation_number.in_(sols), AwardHistory.unit_price > 0)).scalars():
        by_key.setdefault((a.solicitation_number, a.nsn), []).append(a)
    awards = {k: _best_award(v) for k, v in by_key.items()}
    # Verified price level per NSN for derived prices (the same rule as History and the track record).
    derived_nsns = {a.nsn for a in awards.values() if a.source not in RELIABLE_SOURCES and a.source != PILOT_SOURCE}
    verified_median: dict[str, float] = {}
    if derived_nsns:
        by_nsn: dict[str, list[float]] = {}
        for nsn, price in db.execute(select(AwardHistory.nsn, AwardHistory.unit_price).where(
                AwardHistory.nsn.in_(derived_nsns), AwardHistory.source.in_(RELIABLE_SOURCES), AwardHistory.unit_price > 0)).all():
            by_nsn.setdefault(nsn, []).append(float(price))
        verified_median = {k: median(v) for k, v in by_nsn.items()}

    def verified(a: AwardHistory) -> bool:
        if a.source in RELIABLE_SOURCES or a.source == PILOT_SOURCE:
            return True
        ref = verified_median.get(a.nsn)
        return bool(ref and 1 / MAX_RATIO <= float(a.unit_price) / ref <= MAX_RATIO)

    awarded = [(rec, line, sol, awards[(sol.solicitation_number, line.nsn)]) for rec, line, sol in rows
               if (sol.solicitation_number, line.nsn) in awards]
    inputs["awarded"] = len(awarded)
    snaps: dict[int, list[RecommendationSnapshot]] = {}
    ids = [line.id for _, line, _, _ in awarded]
    for i in range(0, len(ids), 500):
        for s in db.execute(
            select(RecommendationSnapshot)
            .where(RecommendationSnapshot.organization_id == org_id,
                   RecommendationSnapshot.solicitation_line_id.in_(ids[i:i + 500]),
                   RecommendationSnapshot.kind == "recommendation")
            .order_by(RecommendationSnapshot.created_at, RecommendationSnapshot.id)
        ).scalars():
            snaps.setdefault(s.solicitation_line_id, []).append(s)
    oem = manufacturer_cages(db)
    out: list[PilotLine] = []
    for rec, line, sol, a in awarded:
        if not verified(a):
            continue
        inputs["verified_price"] += 1
        if a.source == PILOT_SOURCE:
            inputs["outcomes_from_firm"] += 1
        cutoff = datetime.combine(a.award_date, datetime.min.time(), tzinfo=UTC)
        prior = [s for s in snaps.get(line.id, []) if s.created_at is not None and s.created_at < cutoff]
        payload = (prior[-1].payload or {}) if prior else None
        if payload is None and not snaps.get(line.id) and rec.source != HISTORICAL and rec.updated_at is not None \
                and rec.updated_at < cutoff and (rec.no_bid or rec.recommended_bid is not None):
            # The same fallback History uses for lines from before decision snapshots existed: the row itself, as long
            # as nothing has touched it since the award (a recompute after the award moves updated_at past the cutoff).
            payload = {"recommended_bid": rec.recommended_bid, "no_bid": rec.no_bid,
                       "landed_cost_per_unit": rec.landed_cost_per_unit, "confidence": (rec.explanation or {}).get("confidence"),
                       "p_winnable": ((rec.explanation or {}).get("contestability") or {}).get("p_winnable")}
            inputs["decisions_from_updated_at"] = inputs.get("decisions_from_updated_at", 0) + 1
        decided = bool(payload) and (bool(payload.get("no_bid")) or payload.get("recommended_bid") is not None)
        inputs["with_pre_award_decision"] += int(decided)
        if rec.actual_cost_per_unit is not None:
            landed, basis = float(rec.actual_cost_per_unit), "actual"
        elif payload and payload.get("landed_cost_per_unit"):
            landed, basis = float(payload["landed_cost_per_unit"]), "estimate"
        elif rec.landed_cost_per_unit:
            landed, basis = float(rec.landed_cost_per_unit), "estimate"
        else:
            inputs["skipped_no_cost_basis"] += 1
            continue
        awardee = (a.awardee_cage or "").upper()
        out.append(PilotLine(
            line_id=line.id, solicitation_number=sol.solicitation_number, nsn=line.nsn, fsc=line.fsc,
            quantity=float(line.quantity or 1.0), winning_price=float(a.unit_price), landed=landed, cost_basis=basis,
            rec_bid=None if (not payload or payload.get("no_bid")) else payload.get("recommended_bid"),
            tool_decided=decided, no_bid=bool(payload and payload.get("no_bid")),
            actual_bid=float(rec.actual_bid) if rec.actual_bid is not None else None,
            firm_won=(awardee == cage) if (cage and awardee) else None,
            confidence=payload.get("confidence") if payload else None,
            outcome_source="firm" if a.source == PILOT_SOURCE else "dibbs", award_date=a.award_date,
            p_winnable=payload.get("p_winnable") if payload else None,
            awardee_is_reseller=(awardee not in oem or awardee == cage) if awardee else None,
            historical=rec.source == HISTORICAL,
        ))
    return out, inputs


def _cost_model(db: Session, org_id: int, s: OrgSettings) -> dict:
    obs = tenant_quote_observations(db, org_id)
    model = TenantCostModel(obs, prior_fraction=s.provisional_cost_fraction, as_of=today_et())
    loo = model.leave_one_out()
    tiers = summarize_loo(loo)
    return {"quotes": len(obs), "scored": len(loo),
            "tiers": {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in tiers.items()}}


def _contestability(db: Session, lines: list[PilotLine]) -> dict:
    oem = manufacturer_cages(db)
    by_fsc = []
    for fsc in sorted({ln.fsc for ln in lines}):
        df = load_fsc_awards(db, fsc)
        if df.empty:
            continue
        r = evaluate_contestability(df, oem, fsc)
        by_fsc.append({"fsc": fsc, "n": int(r.get("n", 0)), "brier": r.get("brier"), "baseline_brier": r.get("baseline_brier"),
                       "reseller_share": r.get("reseller_share")})
    scored = [ln for ln in lines if ln.p_winnable is not None and ln.awardee_is_reseller is not None]
    own: dict = {"n": len(scored)}
    if scored:
        p = np.array([float(ln.p_winnable) for ln in scored])
        y = np.array([1.0 if ln.awardee_is_reseller else 0.0 for ln in scored])
        base = np.array([fsc_competitors(db, ln.fsc).reseller_share for ln in scored])
        own.update(brier=float(np.mean((p - y) ** 2)), baseline_brier=float(np.mean((base - y) ** 2)),
                   reseller_share=float(y.mean()))
    return {"by_fsc": by_fsc, "own_lines": own}


def evaluate_pilot(db: Session, org_id: int) -> dict:
    """The full evaluation as one JSON-able dict (API, report, CLI)."""
    org = db.get(Organization, org_id)
    s = org_settings(db, org_id)
    lines, inputs = collect_pilot_lines(db, org_id)
    summary = summarize(lines, float(s.pursuit_hours_per_line or 0.33))
    cost_model = _cost_model(db, org_id, s)
    contest = _contestability(db, lines) if lines else {"by_fsc": [], "own_lines": {"n": 0}}
    return {
        "org_id": org_id, "org_name": org.name if org else "", "cage_code": org.cage_code if org else None,
        "generated_at": datetime.now(UTC).isoformat(),
        **summary,
        "inputs": inputs,
        "cost_model": cost_model,
        "contestability": contest,
        "caveats": caveats(summary, inputs),
    }


def report_slug(name: str, org_id: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or f"org{org_id}"


def write_pilot_report(db: Session, org_id: int, out_dir: Path | None = None) -> tuple[Path, dict]:
    ev = evaluate_pilot(db, org_id)
    out_dir = out_dir or REPORTS
    out_dir.mkdir(parents=True, exist_ok=True)
    firm = ev["inputs"].get("outcomes_from_firm", 0)
    note = (f"{firm} outcome{'s' if firm != 1 else ''} imported from the firm's records; the rest from DIBBS award data."
            if firm else "All outcomes from DIBBS award data.")
    text = render_report(ev, ev["org_name"], date.today(), inputs=ev["inputs"], cost_model=ev["cost_model"],
                         contestability=ev["contestability"], data_note=note)
    path = out_dir / f"pilot_{report_slug(ev['org_name'], org_id)}.md"
    path.write_text(text + "\n", encoding="utf-8")
    return path, ev


# ------------------------------------------------------------------------------------------ model promotion check
def pilot_lines_for_training(db: Session) -> list[tuple[PilotLine, OrgSettings]]:
    """Every organization's awarded lines with a pre-award decision, each with its organization's bidding rules."""
    out: list[tuple[PilotLine, OrgSettings]] = []
    for org_id in db.execute(select(Organization.id)).scalars().all():
        lines, _ = collect_pilot_lines(db, org_id)
        s = org_settings(db, org_id)
        out.extend((ln, s) for ln in lines if ln.tool_decided)
    return out
