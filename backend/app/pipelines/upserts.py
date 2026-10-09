"""Idempotent upserts from parsed DIBBS data into the database."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.parsers.dibbs_files import ApprovedSourceRow, BqRow, IndexLine
from app.connectors.parsers.dibbs_html import RecordPage
from app.connectors.parsers.dibbs_pdf import HistoryRow, RfqPdf
from app.models import ApprovedSource, AwardHistory, NsnItem, Solicitation, SolicitationLine

# Acquisition Method Suffix Codes that restrict a buy to approved sources (DoD AMSC definitions:
# B restricted/data not available, C engineering source approval, D data not physically available,
# H directed sole source, P proprietary data, R government doesn't own data).
RESTRICTED_AMSC = {"B", "C", "D", "H", "P", "R"}
SOURCE_CONTROLLED_DESC = {"B"}  # bq item-description indicator: source controlled item


def is_approved_source_only(amsc: str | None, desc_indicator: str | None = None) -> bool:
    return (amsc or "").upper() in RESTRICTED_AMSC or (desc_indicator or "").upper() in SOURCE_CONTROLLED_DESC


def upsert_nsn_item(db: Session, nsn: str, nomenclature: str | None, uoi: str | None, amsc: str | None) -> NsnItem:
    item = db.get(NsnItem, nsn)
    if item is None:
        item = NsnItem(nsn=nsn, fsc=nsn[:4])
        db.add(item)
    if nomenclature and (not item.nomenclature or len(nomenclature) > len(item.nomenclature)):
        item.nomenclature = nomenclature
    item.unit_of_issue = uoi or item.unit_of_issue
    item.amsc = amsc or item.amsc
    item.last_seen_at = datetime.now(UTC)
    return item


def upsert_approved_sources(db: Session, rows: Iterable[tuple[str, str, str]], source: str) -> int:
    """rows: (nsn, cage, part_number)."""
    n = 0
    for nsn, cage, pn in rows:
        if not (nsn and cage and pn):
            continue
        exists = db.execute(
            select(ApprovedSource.id).where(
                ApprovedSource.nsn == nsn, ApprovedSource.cage_code == cage, ApprovedSource.part_number == pn
            )
        ).first()
        if not exists:
            db.add(ApprovedSource(nsn=nsn, cage_code=cage, part_number=pn[:100], source=source))
            db.flush()
            n += 1
    return n


def upsert_from_index(
    db: Session, lines: list[IndexLine], bq_rows: list[BqRow], post_date: date | None = None
) -> tuple[Solicitation, list[SolicitationLine]]:
    """One solicitation's index lines (+ bq CLIN rows) -> Solicitation + lines. Returns new lines."""
    first = lines[0]
    sn = first.solicitation_number
    sol = db.execute(select(Solicitation).where(Solicitation.solicitation_number == sn)).scalar_one_or_none()
    bq_by_pr: dict[str, list[BqRow]] = {}
    for r in bq_rows:
        bq_by_pr.setdefault(r.purchase_request, []).append(r)
    any_bq = next(iter(bq_rows), None)
    if sol is None:
        sol = Solicitation(solicitation_number=sn, type_code=first.type_code or None)
        db.add(sol)
    # A repost with a later return date (amendment/extension) wins.
    if first.return_by_date and (sol.return_by_date is None or first.return_by_date >= sol.return_by_date):
        sol.return_by_date = first.return_by_date
    sol.buyer_code = sol.buyer_code or first.buyer_code or None
    sol.set_aside = first.set_aside
    sol.pdf_url = sol.pdf_url or first.pdf_url
    if any_bq:
        sol.fast_award = sol.fast_award or any_bq.type_indicator == "F"
        sol.aidc = sol.aidc or any_bq.type_indicator == "I" or first.type_code == "U"
        sol.fob = sol.fob or any_bq.fob
        sol.inspection_point = sol.inspection_point or any_bq.inspection_point
    elif first.type_code == "U":
        sol.aidc = True
    raw = dict(sol.raw_json or {})
    raw.setdefault("index", [])
    raw["index"] = [x for x in raw["index"] if x.get("pr") not in {ln.purchase_request for ln in lines}] + [
        {"pr": ln.purchase_request, "item": ln.item, "qty": ln.quantity, "post_date": str(post_date) if post_date else None,
         "buyer": ln.buyer_code, "amsc": ln.amsc, "set_aside": ln.set_aside_code}
        for ln in lines
    ]
    if any_bq:
        raw["bq"] = {"type_indicator": any_bq.type_indicator, "aidc_terms": any_bq.aidc_terms,
                     "higher_level_quality": any_bq.higher_level_quality,
                     "item_description_indicator": any_bq.item_description_indicator}
    sol.raw_json = raw
    db.flush()

    existing = {ln.purchase_request: ln for ln in sol.lines}
    next_no = max((ln.line_no for ln in sol.lines), default=0) + 1
    new: list[SolicitationLine] = []
    for il in lines:
        if il.item_type != "1" or len(il.item) != 13:
            continue  # part-number items have no NSN/FSC
        bq = (bq_by_pr.get(il.purchase_request) or [None])[0]
        line = existing.get(il.purchase_request)
        if line is None:
            line = SolicitationLine(solicitation_id=sol.id, line_no=next_no, purchase_request=il.purchase_request)
            next_no += 1
            db.add(line)
            sol.lines.append(line)
            new.append(line)
        line.nsn = il.item
        line.fsc = il.fsc
        line.niin = il.item[4:]
        line.nomenclature = line.nomenclature or il.nomenclature
        line.quantity = float(il.quantity or 1)
        line.unit_of_issue = il.unit_of_issue
        line.amsc = il.amsc or None
        line.approved_source_only = is_approved_source_only(il.amsc, bq.item_description_indicator if bq else None)
        if bq and bq.delivery_days:
            line.delivery_days = bq.delivery_days
        upsert_nsn_item(db, il.item, il.nomenclature, il.unit_of_issue, il.amsc)
    db.flush()
    return sol, new


def apply_record_page(db: Session, sol: Solicitation, rec: RecordPage) -> None:
    sol.status = rec.status or sol.status
    sol.issue_date = rec.issue_date or sol.issue_date
    sol.return_by_date = rec.return_by_date or sol.return_by_date
    sol.fast_award = sol.fast_award or rec.fast_award
    sol.aidc = sol.aidc or rec.aidc
    sol.tech_docs = rec.tech_docs
    sol.pdf_url = rec.pdf_url or sol.pdf_url
    by_pr = {ln.purchase_request: ln for ln in sol.lines}
    for item in rec.items:
        ln = by_pr.get(item.purchase_request)
        if ln is not None and item.nomenclature and len(item.nomenclature) > len(ln.nomenclature or ""):
            ln.nomenclature = item.nomenclature
    if rec.award and len(sol.lines) == 1:
        ln = sol.lines[0]
        if rec.award.total_value and ln.quantity:
            upsert_awards(
                db,
                [
                    {
                        "nsn": ln.nsn,
                        "award_date": rec.award.award_date or date.today(),
                        "contract_number": rec.award.contract_number,
                        "awardee_cage": rec.award.awardee_cage,
                        "quantity": ln.quantity,
                        "unit_of_issue": ln.unit_of_issue,
                        "unit_price": round(rec.award.total_value / ln.quantity, 5),
                        "total_value": rec.award.total_value,
                        "solicitation_number": sol.solicitation_number,
                        "source": "record_page",
                    }
                ],
            )


def apply_rfq_pdf(db: Session, sol: Solicitation, pdf: RfqPdf) -> dict:
    sol.issue_date = sol.issue_date or pdf.issue_date
    sol.return_by_date = sol.return_by_date or pdf.return_by_date
    sol.buyer_name = pdf.buyer_name or sol.buyer_name
    sol.buyer_email = pdf.buyer_email or sol.buyer_email
    sol.buyer_phone = pdf.buyer_phone or sol.buyer_phone
    sol.naics = pdf.naics or sol.naics
    sol.fast_award = sol.fast_award or pdf.fast_award
    sol.aidc = sol.aidc or pdf.aidc
    sol.first_article = sol.first_article or pdf.first_article
    sol.inspection_point = pdf.inspection_point or sol.inspection_point
    sol.fob = pdf.fob or sol.fob
    sol.packaging = pdf.packaging or sol.packaging
    if (not sol.set_aside or sol.set_aside == "NONE") and pdf.set_aside_notice:
        sol.set_aside = pdf.set_aside_notice
    raw = dict(sol.raw_json or {})
    raw["pdf"] = {"aidc_terms": pdf.aidc_terms, "buyer_code": pdf.buyer_code, "no_history": pdf.no_history,
                  "header_delivery_days": pdf.header_delivery_days}
    sol.raw_json = raw
    by_pr = {ln.purchase_request: ln for ln in sol.lines}
    by_nsn = {ln.nsn: ln for ln in sol.lines}
    for clin in pdf.clins:
        ln = by_pr.get(clin.purchase_request or "") or by_nsn.get(clin.nsn or "")
        if ln is not None and clin.delivery_days and not ln.delivery_days:
            ln.delivery_days = clin.delivery_days
    nsns = {ln.nsn for ln in sol.lines}
    sources = [(nsn, cage, pn) for cage, pn, _ in pdf.approved_sources for nsn in (nsns if len(nsns) == 1 else [])]
    n_src = upsert_approved_sources(db, sources, "rfq_pdf")
    n_hist = upsert_awards(db, [history_to_award(h) for h in pdf.history])
    sol.details_fetched_at = datetime.now(UTC)
    return {"approved_sources": n_src, "history_rows": n_hist, "companies": {c: name for c, _, name in pdf.approved_sources}}


def history_to_award(h: HistoryRow) -> dict:
    return {
        "nsn": h.nsn,
        "award_date": h.award_date,
        "contract_number": h.contract_number,
        "awardee_cage": h.awardee_cage,
        "quantity": h.quantity,
        "unit_of_issue": None,
        "unit_price": h.unit_price,
        "total_value": round(h.unit_price * h.quantity, 2),
        "solicitation_number": None,
        "source": "rfq_pdf_history",
    }


def upsert_awards(db: Session, rows: list[dict]) -> int:
    """Insert award rows not yet present (unique on nsn, contract, date, quantity); fill solicitation links."""
    if not rows:
        return 0
    nsns = {r["nsn"] for r in rows}
    existing: dict[tuple, AwardHistory] = {
        (a.nsn, a.contract_number, a.award_date, float(a.quantity)): a
        for a in db.execute(select(AwardHistory).where(AwardHistory.nsn.in_(nsns))).scalars()
    }
    n = 0
    for r in rows:
        if not r.get("unit_price") or r["unit_price"] <= 0 or not r.get("quantity"):
            continue
        key = (r["nsn"], r["contract_number"], r["award_date"], float(r["quantity"]))
        a = existing.get(key)
        if a is not None:
            if r.get("solicitation_number") and not a.solicitation_number:
                a.solicitation_number = r["solicitation_number"]
            if r.get("unit_of_issue") and not a.unit_of_issue:
                a.unit_of_issue = r["unit_of_issue"]
            continue
        a = AwardHistory(**{k: r.get(k) for k in ("nsn", "award_date", "contract_number", "awardee_cage", "quantity",
                                                   "unit_of_issue", "unit_price", "total_value", "solicitation_number",
                                                   "source")})
        db.add(a)
        existing[key] = a
        n += 1
    db.flush()
    return n


def approved_rows_for(as_rows: list[ApprovedSourceRow], nsns: set[str]) -> list[tuple[str, str, str]]:
    return [(r.nsn, r.cage_code, r.part_number) for r in as_rows if r.nsn in nsns]
