"""Daily RFQ ingest (scheduled 06:00 America/New_York; manual refresh from /admin).

1. Accept the DoD banner (live mode), list posting dates, take today's and any missed index files.
2. Upsert solicitations + lines, filtered to the union of all organizations' FSC watchlists.
3. For each new solicitation: record page + RFQ PDF -> details, procurement history, approved sources.
4. Price prediction for each new line.
5. For each organization whose watchlist matches: draft quote requests + initial recommendations.
6. Record an ingestion_runs row with counts and errors.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.dibbs import DibbsSource, get_dibbs_source
from app.connectors.parsers.dibbs_files import parse_bq_zip
from app.connectors.parsers.dibbs_html import parse_record_page
from app.connectors.parsers.dibbs_pdf import parse_rfq_pdf_text, pdf_to_text
from app.core.timeutil import today_et
from app.models import (
    Company,
    IngestCheckpoint,
    IngestionRun,
    Organization,
    OrgSettings,
    Solicitation,
    SolicitationLine,
)
from app.pipelines.upserts import (
    apply_record_page,
    apply_rfq_pdf,
    approved_rows_for,
    upsert_approved_sources,
    upsert_from_index,
)

log = logging.getLogger(__name__)
LOOKBACK_DAYS = 10


def watched_fscs(db: Session) -> set[str]:
    fscs: set[str] = set()
    for s in db.execute(select(OrgSettings)).scalars():
        fscs.update(s.fsc_watchlist or [])
    return fscs or set(get_settings().default_fsc_watchlist)


def checkpoint_done(db: Session, source: str, key: str) -> bool:
    return bool(
        db.execute(
            select(IngestCheckpoint.id).where(IngestCheckpoint.source == source, IngestCheckpoint.key == key,
                                              IngestCheckpoint.status == "done")
        ).first()
    )


def mark_checkpoint(db: Session, source: str, key: str, status: str = "done", detail: str | None = None) -> None:
    row = db.execute(
        select(IngestCheckpoint).where(IngestCheckpoint.source == source, IngestCheckpoint.key == key)
    ).scalar_one_or_none()
    if row is None:
        db.add(IngestCheckpoint(source=source, key=key, status=status, detail=detail))
    else:
        row.status, row.detail = status, detail


def ingest_index_date(db: Session, src: DibbsSource, d: date, fscs: set[str]) -> tuple[list[SolicitationLine], int]:
    """Upsert one posting date's index (+ bq approved sources). Returns (new lines, matched lines)."""
    lines = [ln for ln in src.index_lines(d) if ln.fsc in fscs and ln.item_type == "1"]
    bq_rows, as_rows = [], []
    bq = src.batch_quote(d)
    if bq:
        bq_rows, as_rows = parse_bq_zip(bq)
    bq_by_sol: dict[str, list] = defaultdict(list)
    for r in bq_rows:
        bq_by_sol[r.solicitation_number].append(r)
    by_sol: dict[str, list] = defaultdict(list)
    for ln in lines:
        by_sol[ln.solicitation_number].append(ln)
    new: list[SolicitationLine] = []
    for sn, group in by_sol.items():
        _, added = upsert_from_index(db, group, bq_by_sol.get(sn, []), post_date=d)
        new.extend(added)
    upsert_approved_sources(db, approved_rows_for(as_rows, {ln.item for ln in lines}), "bq_as_file")
    return new, len(lines)


def fetch_details(db: Session, src: DibbsSource, sol: Solicitation, errors: list) -> dict:
    out = {"record": False, "pdf": False, "history_rows": 0}
    try:
        page = src.record_page(sol.solicitation_number)
        if page:
            apply_record_page(db, sol, parse_record_page(page))
            out["record"] = True
    except Exception as exc:
        errors.append({"solicitation": sol.solicitation_number, "step": "record_page", "error": str(exc)[:300]})
    try:
        pdf = src.rfq_pdf(sol.solicitation_number)
        if pdf:
            res = apply_rfq_pdf(db, sol, parse_rfq_pdf_text(pdf_to_text(pdf)))
            out["pdf"] = True
            out["history_rows"] = res["history_rows"]
            for cage, name in res["companies"].items():
                comp = db.get(Company, cage)
                if comp is None:
                    db.add(Company(cage_code=cage, name=name, is_manufacturer_guess=True, source="rfq_pdf"))
                elif not comp.name:
                    comp.name = name
    except Exception as exc:
        errors.append({"solicitation": sol.solicitation_number, "step": "rfq_pdf", "error": str(exc)[:300]})
    if sol.details_fetched_at is None and src.mode != "fixture":
        sol.details_fetched_at = datetime.now(UTC)  # don't retry forever when a source has no details
    db.flush()
    return out


def run_daily_ingest(db: Session, mode: str | None = None, only_date: date | None = None,
                     make_recommendations: bool = True) -> dict:
    src = get_dibbs_source(mode)
    run = IngestionRun(source=f"dibbs_rfq:{src.mode}", status="running", errors=[], details={})
    db.add(run)
    db.commit()
    errors: list[dict] = []
    summary: dict = {"mode": src.mode, "dates": [], "matched_lines": 0, "new_lines": 0, "details_fetched": 0,
                     "history_rows": 0, "predictions": 0, "recommendations": 0}
    try:
        fscs = watched_fscs(db)
        dates = src.rfq_index_dates()
        if only_date:
            dates = [d for d in dates if d == only_date]
        else:
            cutoff = today_et() - timedelta(days=LOOKBACK_DAYS)
            dates = [d for d in dates if d >= cutoff and not checkpoint_done(db, "rfq_index", d.isoformat())]
            if src.mode == "fixture" and not dates:
                dates = [d for d in src.rfq_index_dates() if not checkpoint_done(db, "rfq_index", d.isoformat())]
        new_lines: list[SolicitationLine] = []
        for d in dates:
            try:
                added, matched = ingest_index_date(db, src, d, fscs)
                new_lines.extend(added)
                summary["matched_lines"] += matched
                mark_checkpoint(db, "rfq_index", d.isoformat())
                db.commit()
                summary["dates"].append(d.isoformat())
            except Exception as exc:
                db.rollback()
                log.exception("index %s failed", d)
                errors.append({"date": d.isoformat(), "step": "index", "error": str(exc)[:300]})
        summary["new_lines"] = len(new_lines)

        # Details for new, still-open solicitations (bounded per run).
        today = today_et()
        sols = db.execute(
            select(Solicitation).where(Solicitation.details_fetched_at.is_(None),
                                       Solicitation.return_by_date >= today)
            .order_by(Solicitation.return_by_date)
        ).scalars().all()
        limit = get_settings().detail_fetch_limit if src.mode != "fixture" else len(sols)
        for sol in sols[:limit]:
            res = fetch_details(db, src, sol, errors)
            summary["details_fetched"] += int(res["record"] or res["pdf"])
            summary["history_rows"] += res["history_rows"]
            db.commit()

        summary["companies_resolved"] = resolve_new_companies(db, src, errors)
        db.commit()
        if make_recommendations and new_lines:
            summary.update(_predict_and_recommend(db, [ln.id for ln in new_lines]))
        run.status = "success" if not errors else "partial"
    except Exception as exc:
        db.rollback()
        log.exception("ingest failed")
        errors.append({"step": "run", "error": str(exc)[:500]})
        run = db.get(IngestionRun, run.id)
        run.status = "failed"
    run.finished_at = datetime.now(UTC)
    run.rows_ingested = summary["new_lines"]
    run.errors = errors[:200]
    run.details = summary
    db.commit()
    summary["errors"] = len(errors)
    summary["status"] = run.status
    return summary


MAX_COMPANY_LOOKUPS = 60


def resolve_new_companies(db: Session, src: DibbsSource, errors: list) -> int:
    """Resolve names for awardee / approved-source CAGEs seen on open lines (bounded per run)."""
    from app.connectors.companies import resolve_company
    from app.models import ApprovedSource, AwardHistory

    today = today_et()
    open_nsns = select(SolicitationLine.nsn).join(Solicitation).where(Solicitation.return_by_date >= today)
    cages = {c for (c,) in db.execute(select(AwardHistory.awardee_cage).where(AwardHistory.nsn.in_(open_nsns))).all() if c}
    cages |= {c for (c,) in db.execute(select(ApprovedSource.cage_code).where(ApprovedSource.nsn.in_(open_nsns))).all()}
    known = {c for (c,) in db.execute(select(Company.cage_code).where(Company.name.is_not(None))).all()}
    n = 0
    for cage in sorted(cages - known)[:MAX_COMPANY_LOOKUPS]:
        try:
            if resolve_company(db, src, cage) and db.get(Company, cage).name:
                n += 1
        except Exception as exc:
            errors.append({"cage": cage, "step": "company", "error": str(exc)[:200]})
    db.flush()
    return n


def _predict_and_recommend(db: Session, line_ids: list[int]) -> dict:
    from app.services.pricing import fsc_median_index, predict_line
    from app.services.recommendations import refresh_org_recommendations

    today = today_et()
    lines = [
        ln for ln in db.execute(select(SolicitationLine).where(SolicitationLine.id.in_(line_ids))).scalars()
        if ln.solicitation.return_by_date is None or ln.solicitation.return_by_date >= today
    ]
    out = {"predictions": 0, "recommendations": 0}
    if not lines:
        return out
    idx = fsc_median_index(db, {ln.fsc for ln in lines}, today)
    for ln in lines:
        predict_line(db, ln, fsc_index=idx)
        out["predictions"] += 1
    db.commit()
    internal = {o.id for o in db.execute(select(Organization).where(Organization.plan == "internal")).scalars()}
    for s in db.execute(select(OrgSettings)).scalars().all():
        if s.organization_id in internal:
            continue
        ids = [ln.id for ln in lines if ln.fsc in (s.fsc_watchlist or [])]
        if ids:
            out["recommendations"] += refresh_org_recommendations(db, s.organization_id, auto_quotes=True, line_ids=ids)
            db.commit()
    return out
