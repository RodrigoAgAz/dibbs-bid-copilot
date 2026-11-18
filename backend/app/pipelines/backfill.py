"""Resumable historical backfill:  make backfill FSC=2530,1005 START=2023-01-01 END=today

What DIBBS makes publicly available shapes this (docs/data_sources.md):
  * RFQ index files only for the last ~30 days of postings, award listings for ~45 days;
  * but every automated (T/U) RFQ PDF carries the NSN's procurement history, often 10+ years deep.
So the backfill:
  1. walks every available RFQ posting date in [START, END] for the FSCs, upserting solicitations;
  2. fetches ONE RFQ PDF per NSN (an automated one when possible) and stores its procurement history
     -> award_history rows back to START (this is where the 2+ years of history come from);
  3. walks award listings by posting date in [START, END] for the FSCs (line prices from PDFs, bounded);
  4. runs an NSN award search (Scope=all) for NSNs that got no PDF history.
Every unit of work is checkpointed in ingest_checkpoints, so re-running resumes where it stopped.
Running the daily ingest for a few weeks keeps extending NSN coverage.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.dibbs import get_dibbs_source
from app.connectors.parsers.dibbs_html import parse_award_listing
from app.connectors.parsers.dibbs_pdf import parse_rfq_pdf_text, pdf_to_text
from app.models import AwardHistory, IngestionRun, Solicitation, SolicitationLine
from app.pipelines.awards import award_rows_from_listing
from app.pipelines.ingest import checkpoint_done, ingest_index_date, mark_checkpoint
from app.pipelines.upserts import apply_rfq_pdf, history_to_award, upsert_awards

log = logging.getLogger(__name__)
MAX_PDFS_PER_RUN = 2000
MAX_AWARD_PDFS = 300


def run_backfill(db: Session, fscs: list[str], start: date, end: date, mode: str | None = None,
                 max_pdfs: int = MAX_PDFS_PER_RUN, max_award_dates: int = 10, max_award_pages: int = 20,
                 max_nsn_searches: int = 50) -> dict:
    src = get_dibbs_source(mode)
    run = IngestionRun(source=f"backfill:{src.mode}", status="running", errors=[], details={"fscs": fscs})
    db.add(run)
    db.commit()
    fset = set(fscs)
    errors: list[dict] = []
    s = {"mode": src.mode, "fscs": sorted(fset), "start": start.isoformat(), "end": end.isoformat(), "index_dates": 0,
         "nsn_pdfs": 0, "history_rows": 0, "award_post_dates": 0, "award_rows": 0, "nsn_searches": 0}
    try:
        # 1. RFQ index dates.
        for d in src.rfq_index_dates():
            key = f"{d.isoformat()}:{','.join(sorted(fset))}"
            if not (start <= d <= end) or checkpoint_done(db, "backfill_index", key):
                continue
            try:
                ingest_index_date(db, src, d, fset)
                mark_checkpoint(db, "backfill_index", key)
                db.commit()
                s["index_dates"] += 1
            except Exception as exc:
                db.rollback()
                errors.append({"step": "index", "date": d.isoformat(), "error": str(exc)[:300]})

        # 2. One PDF per NSN for procurement history (prefer automated T/U solicitations).
        rows = db.execute(
            select(SolicitationLine.nsn, Solicitation.solicitation_number, Solicitation.type_code, Solicitation.id)
            .join(Solicitation).where(SolicitationLine.fsc.in_(fset))
            .order_by(Solicitation.type_code.desc())
        ).all()
        best: dict[str, tuple[str, int]] = {}
        for nsn, sn, tc, sid in rows:
            if nsn not in best or (tc in ("T", "U") and best[nsn][0][8:9] == "Q"):
                best[nsn] = (sn, sid)
        # NSNs whose procurement history already came in with a daily-ingest PDF need no second fetch.
        have_pdf_history = {
            n for (n,) in db.execute(
                select(AwardHistory.nsn).where(AwardHistory.source == "rfq_pdf_history").distinct()
            ).all()
        }
        budget = max_pdfs
        for nsn, (sn, sid) in best.items():
            if budget <= 0:
                break
            if nsn in have_pdf_history or checkpoint_done(db, "backfill_nsn_pdf", nsn):
                continue
            budget -= 1
            try:
                data = src.rfq_pdf(sn)
                if data:
                    sol = db.get(Solicitation, sid)
                    pdf = parse_rfq_pdf_text(pdf_to_text(data))
                    apply_rfq_pdf(db, sol, pdf)
                    rows_in_range = [history_to_award(h) for h in pdf.history if start <= h.award_date <= end]
                    s["history_rows"] += upsert_awards(db, rows_in_range)
                    s["nsn_pdfs"] += 1
                mark_checkpoint(db, "backfill_nsn_pdf", nsn, detail=sn)
                db.commit()
            except Exception as exc:
                db.rollback()
                mark_checkpoint(db, "backfill_nsn_pdf", nsn, status="failed", detail=str(exc)[:200])
                db.commit()
                errors.append({"step": "nsn_pdf", "nsn": nsn, "error": str(exc)[:300]})

        # 3. Award listings by posting date.
        pdf_budget = [MAX_AWARD_PDFS]
        award_dates = [d for d in src.award_dates()
                       if start <= d <= end and not checkpoint_done(db, "backfill_award_date", d.isoformat())]
        for d in sorted(award_dates)[-max_award_dates:]:
            try:
                lrows = []
                for page in src.awards_by_post_date(d, max_pages=max_award_pages):
                    lrows.extend(parse_award_listing(page).rows)
                s["award_rows"] += upsert_awards(db, award_rows_from_listing(db, src, lrows, fset, pdf_budget))
                mark_checkpoint(db, "backfill_award_date", d.isoformat())
                db.commit()
                s["award_post_dates"] += 1
            except Exception as exc:
                db.rollback()
                errors.append({"step": "award_date", "date": d.isoformat(), "error": str(exc)[:300]})

        # 4. NSN award search for NSNs still without history.
        have = {n for (n,) in db.execute(select(AwardHistory.nsn).distinct()).all()}
        missing = [n for n in best if n not in have and not checkpoint_done(db, "backfill_nsn_search", n)]
        for i in range(0, min(len(missing), 40 * max_nsn_searches), 40):
            chunk = missing[i : i + 40]
            try:
                lrows = []
                for page in src.award_search("nsn", chunk, scope="all", max_pages=max_award_pages):
                    lrows.extend(r for r in parse_award_listing(page).rows if r.nsn in set(chunk))
                lrows = [r for r in lrows if r.award_date and start <= r.award_date <= end]
                s["award_rows"] += upsert_awards(db, award_rows_from_listing(db, src, lrows, fset, pdf_budget))
                for n in chunk:
                    mark_checkpoint(db, "backfill_nsn_search", n)
                db.commit()
                s["nsn_searches"] += 1
            except Exception as exc:
                db.rollback()
                errors.append({"step": "nsn_search", "error": str(exc)[:300]})
        run.status = "success" if not errors else "partial"
    except Exception as exc:
        db.rollback()
        log.exception("backfill failed")
        errors.append({"step": "run", "error": str(exc)[:500]})
        run = db.get(IngestionRun, run.id)
        run.status = "failed"
    run.finished_at = datetime.now(UTC)
    run.rows_ingested = s["history_rows"] + s["award_rows"]
    run.errors = errors[:200]
    run.details = s
    db.commit()
    try:
        from app.pipelines.fsc_report import write_fsc_volume_report

        s["fsc_report"] = str(write_fsc_volume_report(db))
    except Exception as exc:  # report is best-effort
        errors.append({"step": "fsc_report", "error": str(exc)[:300]})
    s["errors"] = len(errors)
    s["status"] = run.status
    return s
