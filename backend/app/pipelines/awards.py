"""Award labeling (daily): attach newly posted awards to solicitations.

Last month's predictions become this month's labeled data, and /history can show won/lost and the
winning price for lines a broker tracked.

Sources, cheapest first:
  1. the RFQ record page of each tracked solicitation (adds an "Awarded" row with CAGE, contract,
     date and total value) - for single-line solicitations unit price = total / quantity;
  2. the award listing by posting date (new posting dates only), filtered to watched FSCs;
  3. award PDFs for line-level quantity and unit price when the total alone is ambiguous (bounded).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.connectors.dibbs import DibbsSource, get_dibbs_source
from app.connectors.parsers.dibbs_html import AwardListingRow, parse_award_listing, parse_record_page
from app.connectors.parsers.dibbs_pdf import parse_award_pdf_text, pdf_to_text
from app.core.timeutil import today_et
from app.models import AwardHistory, IngestionRun, Recommendation, Solicitation, SolicitationLine
from app.pipelines.ingest import checkpoint_done, mark_checkpoint, watched_fscs
from app.pipelines.upserts import apply_record_page, upsert_awards

log = logging.getLogger(__name__)
TRACK_DAYS = 75
MAX_AWARD_PDFS = 150


def award_rows_from_listing(
    db: Session, src: DibbsSource, rows: list[AwardListingRow], fscs: set[str], pdf_budget: list[int]
) -> list[dict]:
    """Listing rows -> award_history dicts (unit price from our line qty, else from the award PDF)."""
    out: list[dict] = []
    for r in rows:
        if len(r.nsn) != 13 or not r.nsn.isdigit() or r.nsn[:4] not in fscs or not r.award_date:
            continue
        line = None
        if r.solicitation_number:
            line = db.execute(
                select(SolicitationLine).join(Solicitation).where(
                    Solicitation.solicitation_number == r.solicitation_number, SolicitationLine.nsn == r.nsn
                )
            ).scalars().first()
        base = {
            "nsn": r.nsn,
            "award_date": r.award_date,
            "contract_number": r.contract_number,
            "awardee_cage": r.awardee_cage or None,
            "solicitation_number": r.solicitation_number or None,
            "source": "award_listing",
        }
        single = line is not None and len(line.solicitation.lines) == 1
        if single and r.total_price and line.quantity:
            out.append({**base, "quantity": line.quantity, "unit_of_issue": line.unit_of_issue,
                        "unit_price": round(r.total_price / line.quantity, 5), "total_value": r.total_price})
            continue
        if pdf_budget[0] <= 0 or not r.pdf_url:
            continue
        pdf_budget[0] -= 1
        try:
            data = src.award_pdf(r.pdf_url)
        except Exception as exc:
            log.warning("award PDF %s failed: %s", r.pdf_url, exc)
            continue
        if not data:
            continue
        parsed = parse_award_pdf_text(pdf_to_text(data))
        lines = [ln for ln in parsed.lines if ln.quantity > 0]
        match = [ln for ln in lines if ln.item == r.nsn] or (lines if len(lines) == 1 else [])
        for ln in match[:1]:
            out.append({**base, "quantity": ln.quantity, "unit_of_issue": ln.unit_of_issue, "unit_price": ln.unit_price,
                        "total_value": ln.total, "source": "award_pdf"})
    return out


def run_award_labeling(db: Session, mode: str | None = None, max_post_dates: int = 5) -> dict:
    src = get_dibbs_source(mode)
    run = IngestionRun(source=f"dibbs_awards:{src.mode}", status="running", errors=[], details={})
    db.add(run)
    db.commit()
    errors: list[dict] = []
    summary = {"mode": src.mode, "record_pages": 0, "awarded_from_records": 0, "post_dates": [], "listing_rows": 0,
               "award_rows_added": 0}
    try:
        today = today_et()
        fscs = watched_fscs(db)
        # 1. Tracked solicitations without an award yet.
        tracked = db.execute(
            select(Solicitation).where(
                Solicitation.return_by_date >= today - timedelta(days=TRACK_DAYS),
                or_(Solicitation.status.is_(None), Solicitation.status != "Awarded"),
            )
        ).scalars().all()
        tracked_ids = {
            sid for (sid,) in db.execute(
                select(SolicitationLine.solicitation_id).join(Recommendation, Recommendation.solicitation_line_id == SolicitationLine.id)
            ).all()
        }
        for sol in tracked:
            if sol.id not in tracked_ids and src.mode != "fixture":
                continue
            has_award = db.execute(
                select(AwardHistory.id).where(AwardHistory.solicitation_number == sol.solicitation_number)
            ).first()
            if has_award:
                continue
            try:
                page = src.record_page(sol.solicitation_number)
            except Exception as exc:
                errors.append({"solicitation": sol.solicitation_number, "error": str(exc)[:200]})
                continue
            if not page:
                continue
            summary["record_pages"] += 1
            rec = parse_record_page(page)
            apply_record_page(db, sol, rec)
            if rec.award:
                summary["awarded_from_records"] += 1
            db.commit()
        # 2. New award posting dates.
        budget = [MAX_AWARD_PDFS]
        dates = [d for d in src.award_dates() if not checkpoint_done(db, "award_post_date", d.isoformat())]
        for d in sorted(dates)[-max_post_dates:]:
            try:
                rows = []
                for page in src.awards_by_post_date(d):
                    rows.extend(parse_award_listing(page).rows)
                summary["listing_rows"] += len(rows)
                summary["award_rows_added"] += upsert_awards(db, award_rows_from_listing(db, src, rows, fscs, budget))
                mark_checkpoint(db, "award_post_date", d.isoformat())
                db.commit()
                summary["post_dates"].append(d.isoformat())
            except Exception as exc:
                db.rollback()
                errors.append({"post_date": d.isoformat(), "error": str(exc)[:300]})
        run.status = "success" if not errors else "partial"
    except Exception as exc:
        db.rollback()
        log.exception("award labeling failed")
        errors.append({"step": "run", "error": str(exc)[:500]})
        run = db.get(IngestionRun, run.id)
        run.status = "failed"
    run.finished_at = datetime.now(UTC)
    run.rows_ingested = summary["award_rows_added"] + summary["awarded_from_records"]
    run.errors = errors[:200]
    run.details = summary
    db.commit()
    summary["errors"] = len(errors)
    summary["status"] = run.status
    return summary

