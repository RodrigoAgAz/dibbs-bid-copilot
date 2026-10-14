"""Award labeling (daily): attach newly posted awards to solicitations.

Last month's predictions become this month's labeled data, and /history can show won/lost and the
winning price for lines a broker tracked.

Sources, cheapest first:
  1. the RFQ record page of tracked solicitations once they close (adds an "Awarded" row with CAGE,
     contract, date and total value) - for single-line solicitations unit price = total / quantity.
     Bounded per run and re-checked with backoff (2 / 5 / 14 days as the solicitation ages), lines a
     broker bid on first;
  2. the award listing by posting date (new posting dates only), filtered to watched FSCs;
  3. award PDFs for line-level quantity and unit price when the total alone is ambiguous (bounded).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.connectors.dibbs import DibbsSource, get_dibbs_source
from app.connectors.parsers.dibbs_html import AwardListingRow, parse_award_listing, parse_record_page
from app.connectors.parsers.dibbs_pdf import parse_award_pdf_text, pdf_to_text
from app.core.timeutil import today_et
from app.models import (
    AwardHistory,
    IngestionRun,
    QuoteRequest,
    QuoteRequestStatus,
    Recommendation,
    Solicitation,
    SolicitationLine,
)
from app.pipelines.ingest import checkpoint_done, mark_checkpoint, watched_fscs
from app.pipelines.upserts import apply_record_page, upsert_awards

log = logging.getLogger(__name__)
TRACK_DAYS = 75
MAX_AWARD_PDFS = 150
# ~1 request/second: 400 record pages is about 7 minutes per daily run.
MAX_RECORD_PAGES = 400
RECHECK_RECENT = timedelta(days=2)
RECHECK_MID = timedelta(days=5)
RECHECK_OLD = timedelta(days=14)
TERMINAL_STATUSES = ("Awarded", "Cancelled", "Canceled")


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


def _recheck_interval(days_closed: int) -> timedelta:
    """Back off record-page checks as a closed solicitation ages (most awards post within ~2 weeks)."""
    if days_closed <= 14:
        return RECHECK_RECENT
    if days_closed <= 45:
        return RECHECK_MID
    return RECHECK_OLD


def solicitations_due_for_award_check(db: Session, today: date, now: datetime, limit: int) -> list[Solicitation]:
    """Closed, tracked solicitations without an award, whose last check is older than their recheck interval.

    Ordered by business value: lines a broker actually bid on (or sent quote requests for) first, then the
    most recently closed. Open solicitations are skipped: nothing can be awarded before the return-by date.
    """
    acted = {
        sid for (sid,) in db.execute(
            select(SolicitationLine.solicitation_id)
            .join(Recommendation, Recommendation.solicitation_line_id == SolicitationLine.id)
            .where(Recommendation.actual_bid.is_not(None))
        ).all()
    } | {
        sid for (sid,) in db.execute(
            select(SolicitationLine.solicitation_id)
            .join(QuoteRequest, QuoteRequest.solicitation_line_id == SolicitationLine.id)
            .where(QuoteRequest.status != QuoteRequestStatus.draft)
        ).all()
    }
    tracked = {
        sid for (sid,) in db.execute(
            select(SolicitationLine.solicitation_id)
            .join(Recommendation, Recommendation.solicitation_line_id == SolicitationLine.id)
        ).all()
    }
    awarded = {
        sn for (sn,) in db.execute(
            select(AwardHistory.solicitation_number).where(AwardHistory.solicitation_number.is_not(None))
        ).all()
    }
    candidates = db.execute(
        select(Solicitation).where(
            Solicitation.return_by_date < today,
            Solicitation.return_by_date >= today - timedelta(days=TRACK_DAYS),
            or_(Solicitation.status.is_(None), Solicitation.status.not_in(TERMINAL_STATUSES)),
        )
    ).scalars().all()
    due = []
    for sol in candidates:
        if sol.id not in tracked or sol.solicitation_number in awarded:
            continue
        if sol.award_checked_at is not None:
            interval = _recheck_interval((today - sol.return_by_date).days)
            if now - sol.award_checked_at < interval:
                continue
        due.append(sol)
    due.sort(key=lambda s: (s.id not in acted, -s.return_by_date.toordinal(), s.solicitation_number))
    return due[:limit]


def run_award_labeling(db: Session, mode: str | None = None, max_post_dates: int = 5,
                       max_record_pages: int = MAX_RECORD_PAGES) -> dict:
    src = get_dibbs_source(mode)
    run = IngestionRun(source=f"dibbs_awards:{src.mode}", status="running", errors=[], details={})
    db.add(run)
    db.commit()
    errors: list[dict] = []
    summary = {"mode": src.mode, "record_pages": 0, "record_pages_due": 0, "awarded_from_records": 0,
               "post_dates": [], "listing_rows": 0, "award_rows_added": 0}
    try:
        today = today_et()
        now = datetime.now(UTC)
        fscs = watched_fscs(db)
        # 1. Record pages of closed, tracked solicitations that have no award yet (bounded, with backoff).
        due = solicitations_due_for_award_check(db, today, now, max_record_pages)
        summary["record_pages_due"] = len(due)
        for sol in due:
            try:
                page = src.record_page(sol.solicitation_number)
            except Exception as exc:
                errors.append({"solicitation": sol.solicitation_number, "error": str(exc)[:200]})
                continue
            sol.award_checked_at = now
            if page:
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

