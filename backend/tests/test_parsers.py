"""Parsers against REAL DIBBS samples captured during discovery (backend/tests/fixtures)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.connectors.parsers.dibbs_files import parse_bq_zip, parse_index, parse_rfq_dates_page, rfq_pdf_url
from app.connectors.parsers.dibbs_html import (
    is_banner,
    parse_award_listing,
    parse_award_package,
    parse_dibbs_cage,
    parse_nsn_page,
    parse_record_page,
)
from app.connectors.parsers.dibbs_pdf import parse_award_pdf_text, parse_rfq_pdf_text, pdf_to_text
from app.connectors.usaspending import parse_search

F = Path(__file__).parent / "fixtures"
D = F / "dibbs"


def txt(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_index_file_fixed_width():
    lines = parse_index((D / "rfq_index" / "in261008.txt").read_bytes())
    stats = json.loads((D / "rfq_index" / "index_stats.json").read_text())
    assert len(lines) == stats["files"]["261008"] == 1769
    first = lines[0]
    assert first.solicitation_number == "SPE1C127Q0039"
    assert first.item == "8345002412243" and first.is_nsn and first.fsc == "8345"
    assert first.purchase_request == "7017746653"
    assert first.return_by_date == date(2026, 10, 15)
    assert first.quantity == 100 and first.unit_of_issue == "EA"
    assert first.nomenclature == "FLAG,INDIVIDUAL" and first.buyer_code == "DBM01"
    assert first.amsc == "G" and first.item_type == "1" and first.set_aside == "NONE"
    assert first.type_code == "Q"
    # CRLF and LF parse identically.
    raw = (D / "rfq_index" / "in261008.txt").read_bytes()
    assert len(parse_index(raw.replace(b"\r\n", b"\n"))) == 1769


def test_index_known_line_and_type_counts():
    lines = []
    for p in sorted((D / "rfq_index").glob("in*.txt")):
        lines += parse_index(p.read_bytes())
    stats = json.loads((D / "rfq_index" / "index_stats.json").read_text())
    assert len(lines) == stats["total_lines"]
    by_type = {}
    for ln in lines:
        by_type[ln.type_code] = by_type.get(ln.type_code, 0) + 1
    assert by_type == stats["lines_by_type_9th_char"]
    screw = next(ln for ln in lines if ln.solicitation_number == "SPE4A627T0486")
    assert (screw.item, screw.quantity, screw.set_aside, screw.amsc) == ("5305011209897", 1977, "WOSB", "G")
    assert screw.pdf_url == "https://dibbs2.bsm.dla.mil/Downloads/RFQ/6/SPE4A627T0486.PDF"
    assert rfq_pdf_url("SPE4A626T36LQ").endswith("/RFQ/Q/SPE4A626T36LQ.PDF")


def test_batch_quote_zip():
    bq, approved = parse_bq_zip((D / "rfq_index" / "bq261008.zip").read_bytes())
    assert len(bq) == 1868 and len(approved) == 1888
    q0061 = [r for r in bq if r.solicitation_number == "SPE4A627Q0061"]
    assert [r.quantity for r in q0061] == [2618.0, 1058.0]
    t0162 = [r for r in bq if r.solicitation_number == "SPE4A027T0162"]
    assert len(t0162) == 18 and t0162[0].type_indicator == "F"
    assert {r.fob for r in bq} <= {"ORIGIN", "DESTINATION"}
    assert approved[0].nsn == "1005012209398" and approved[0].cage_code == "62228"


def test_rfq_dates_page():
    dates = parse_rfq_dates_page((D / "rfq_dates" / "rfqdates_recent.html").read_text(encoding="latin-1"))
    kinds = {k for k, _, _ in dates}
    assert kinds == {"in", "bq"}
    assert ("in", date(2026, 10, 8), "https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/in261008.txt") in dates


def test_record_page_open_and_awarded():
    rec = parse_record_page((D / "rfq_record" / "rfqrec_SPE7L127U0037.html").read_bytes())
    assert rec.solicitation_number == "SPE7L127U0037" and rec.status == "Open"
    assert rec.aidc and not rec.fast_award and rec.set_aside_label == "Small Business Set-Aside"
    assert rec.issue_date == date(2026, 10, 7) and rec.return_by_date == date(2026, 10, 21)
    assert rec.items[0].item == "5340004290174" and rec.items[0].quantity == 12 and rec.tech_docs
    assert rec.pdf_url.endswith("/RFQ/7/SPE7L127U0037.PDF")
    awarded = parse_record_page((D / "rfq_record" / "rfqrec_SPE7M027T0003.html").read_bytes())
    assert awarded.status == "Awarded"
    assert awarded.award.awardee_cage == "1V630" and awarded.award.contract_number == "SPE7M027P0185"
    assert awarded.award.total_value == pytest.approx(240.58) and awarded.award.award_date == date(2026, 10, 9)
    fast = parse_record_page((D / "rfq_record" / "rfqrec_SPE4A027T0162.html").read_bytes())
    assert fast.fast_award and len(fast.items) == 18


def test_rfq_pdf_automated_with_history():
    d = parse_rfq_pdf_text(txt(D / "rfq_pdf" / "SPE4A627T0486.txt"))
    assert d.solicitation_number == "SPE4A627T0486" and d.issue_date == date(2026, 10, 6)
    assert d.return_by_date == date(2026, 10, 13)
    assert (d.buyer_name, d.buyer_code, d.buyer_email) == ("Dana Barefoot", "DDB0227", "dana.barefoot@dla.mil")
    assert d.naics == "332112" and d.fast_award and d.set_aside_notice == "WOSB"
    assert d.inspection_point == "DESTINATION" and d.fob == "ORIGIN" and "MIL-STD-2073" in d.packaging
    assert len(d.clins) == 1 and d.clins[0].quantity == 1977 and d.clins[0].delivery_days == 154
    assert len(d.history) == 20
    h = d.history[0]
    assert (h.nsn, h.awardee_cage, h.contract_number, h.quantity, h.unit_price, h.award_date) == (
        "5305011209897", "6M132", "SPE4A626F470G", 643.0, 13.8, date(2026, 9, 17))
    assert d.history[-1].award_date == date(2015, 10, 27)


def test_rfq_pdf_history_across_page_break_is_merged():
    d = parse_rfq_pdf_text(txt(D / "rfq_pdf" / "SPE7L127U0037.txt"))
    assert d.aidc and d.aidc_terms["contract_maximum_value"] == 350000.0
    keys = {(h.contract_number, h.award_date, h.quantity) for h in d.history}
    assert len(keys) == len(d.history) == 15
    assert d.approved_sources == [("96266", "16-PD-08201-S-NSL", "ALBION INDUSTRIES, INC.")]


def test_rfq_pdf_manual_q_type():
    d = parse_rfq_pdf_text(txt(D / "rfq_pdf" / "SPE4A627Q0061.txt"))
    assert (d.buyer_name, d.buyer_code) == ("MICHAEL BLAIN", "DMB0289")
    assert [c.quantity for c in d.clins] == [2618.0, 1058.0]
    assert d.clins[0].nsn == "5305010560169" and d.clins[0].delivery_days == 120 and d.clins[0].fob == "ORIGIN"
    assert d.history == [] and d.naics is None
    fa = parse_rfq_pdf_text(txt(D / "rfq_pdf" / "SPE4A627Q0058.txt"))
    assert fa.first_article and fa.approved_sources == [("84256", "AS621-102", "AVIBANK MFG., INC.")]
    team = parse_rfq_pdf_text(txt(D / "rfq_pdf" / "SPE7L127Q0024.txt"))
    assert team.buyer_code == "PLCL69Q"


def test_pdf_to_text_on_real_pdf():
    text = pdf_to_text((D / "rfq_pdf" / "SPE7M027T0003.pdf").read_bytes(), max_pages=8)
    d = parse_rfq_pdf_text(text)
    assert d.solicitation_number == "SPE7M027T0003" and d.clins[0].nsn == "6145012249084"


def test_nsn_page():
    p = parse_nsn_page((D / "nsn" / "rfqnsn_5330016630871.html").read_bytes())
    assert (p.nsn, p.nomenclature, p.amsc) == ("5330016630871", "GASKET", "C")
    assert p.approved_sources == [("75Q65", "4241920", "OSHKOSH DEFENSE LLC")]


def test_award_listing_and_paging():
    page1 = parse_award_listing((D / "awards" / "awdrecs_post_10-08-2026_page1.html").read_bytes())
    assert page1.records_found == 4069 and len(page1.rows) == 50
    assert "Page$2" in page1.pages and "__VIEWSTATE" in page1.hidden
    row = page1.rows[0]
    assert row.award_basic == "N0038323GAN01" and row.delivery_order == "SPE4A727F0066"
    assert row.contract_number == "SPE4A727F0066" and row.total_price == pytest.approx(3988.15)
    assert row.pdf_url == "https://dibbs2.bsm.dla.mil/Downloads/Awards/08OCT26/N0038323GAN01SPE4A727F0066.PDF"
    page2 = parse_award_listing((D / "awards" / "awdrecs_post_10-08-2026_page2.html").read_bytes())
    assert {r.award_basic for r in page2.rows}.isdisjoint({r.award_basic + r.delivery_order for r in page1.rows[:1]})
    nsn = parse_award_listing((D / "awards" / "awdrecs_nsn_search_1.html").read_bytes())
    assert nsn.rows[0].solicitation_number == "SPE4A527T0082" and nsn.rows[0].awardee_cage == "2N935"


def test_award_package_and_pdfs():
    pkg = parse_award_package((D / "awards" / "awdrec_SPE7M027P0185.html").read_bytes())
    assert (pkg.awardee_cage, pkg.awardee_name, pkg.price) == ("1V630", "F & R SALES, INC.", 240.58)
    p = parse_award_pdf_text(txt(D / "award_pdf" / "SPE7M027P0185.txt"))
    assert p.contractor_cage == "1V630" and p.total == 240.58
    assert (p.lines[0].item, p.lines[0].quantity, p.lines[0].unit_price, p.lines[0].fmt) == ("6145012249084", 493.0, 0.488, "P")
    v = parse_award_pdf_text(txt(D / "award_pdf" / "SPE4A527V0074.txt"))
    assert (v.lines[0].quantity, v.lines[0].unit_price, v.lines[0].fmt) == (24.0, 180.0, "V")
    assert v.nsns == ["5305011635794"]
    f = parse_award_pdf_text(txt(D / "award_pdf" / "SPE4AX20D9412SPE4A226F1602.txt"))
    assert (f.lines[0].quantity, f.lines[0].unit_price, f.lines[0].fmt) == (455.0, 3.94, "F")
    aidc = parse_award_pdf_text(txt(D / "award_pdf" / "SPE7L126D60JX.txt"))
    assert aidc.lines[0].quantity == 0.0 and aidc.lines[0].unit_price == 16.76


def test_cage_page_and_banner_detection():
    c = parse_dibbs_cage((F / "cage" / "dibbs_cage_3WGD1.html").read_bytes())
    assert (c.name, c.city, c.state) == ("STATZ CORP", "MIDDLETON", "WI")
    assert is_banner((D / "rfq_dates" / "dodwarning_www.html").read_bytes())
    assert is_banner((D / "rfq_dates" / "dodwarning_dibbs2.html").read_bytes())
    assert not is_banner((D / "rfq_record" / "rfqrec_SPE7L127U0037.html").read_bytes())


def test_usaspending_parse():
    data = json.loads((F / "usaspending" / "spending_by_award_piid_keyword.json").read_text())
    awards = parse_search(data["response"])
    assert awards[0].piid == "SPE4A626PL037" and awards[0].amount == 25232.0
