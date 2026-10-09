"""Parsers for DIBBS daily files: RFQ index (inYYMMDD.txt) and batch-quote zip (bqYYMMDD.zip).

Formats are documented (with literal samples) in docs/data_sources.md sections 2-4.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime

INDEX_RECORD_LEN = 140
SET_ASIDE_CODES = {
    "N": "NONE",
    "Y": "SB",
    "H": "HUBZONE",
    "R": "SDVOSB",
    "L": "WOSB",
    "A": "8A",
    "E": "EDWOSB",
}
DATE_LINK_RE = re.compile(r"/Archive/(in|bq)(\d{6})\.(txt|zip)", re.I)


@dataclass
class IndexLine:
    solicitation_number: str
    item: str  # NSN (13 digits) or part number / local stock number
    purchase_request: str
    return_by_date: date | None
    file_name: str
    quantity: int
    unit_of_issue: str
    nomenclature: str
    buyer_code: str
    amsc: str
    item_type: str  # 1 = NSN, 2 = part number
    set_aside_code: str
    set_aside_pct: int

    @property
    def is_nsn(self) -> bool:
        return self.item_type == "1" and len(self.item) == 13 and self.item.isdigit()

    @property
    def fsc(self) -> str:
        return self.item[:4]

    @property
    def type_code(self) -> str:
        return self.solicitation_number[8] if len(self.solicitation_number) >= 9 else ""

    @property
    def set_aside(self) -> str:
        return SET_ASIDE_CODES.get(self.set_aside_code, self.set_aside_code or "NONE")

    @property
    def pdf_url(self) -> str:
        return rfq_pdf_url(self.solicitation_number)


def rfq_pdf_url(solicitation_number: str) -> str:
    sn = solicitation_number.replace("-", "").upper()
    return f"https://dibbs2.bsm.dla.mil/Downloads/RFQ/{sn[-1]}/{sn}.PDF"


def _mdy(s: str, fmt: str) -> date | None:
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, fmt).date()
    except ValueError:
        return None


def parse_index(data: bytes | str) -> list[IndexLine]:
    """Fixed-width RFQ index. Accepts CRLF or LF, ignores blank/short lines."""
    text = data.decode("latin-1") if isinstance(data, bytes) else data
    out: list[IndexLine] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if len(line) < INDEX_RECORD_LEN:
            line = line.ljust(INDEX_RECORD_LEN)
        qty = line[99:106].strip()
        pct = line[137:140].strip()
        out.append(
            IndexLine(
                solicitation_number=line[0:13].strip(),
                item=line[13:59].strip(),
                purchase_request=line[59:72].strip(),
                return_by_date=_mdy(line[72:80], "%m/%d/%y"),
                file_name=line[80:99].strip(),
                quantity=int(qty) if qty.isdigit() else 0,
                unit_of_issue=line[106:108].strip(),
                nomenclature=line[108:129].strip(),
                buyer_code=line[129:134].strip(),
                amsc=line[134:135].strip(),
                item_type=line[135:136].strip(),
                set_aside_code=line[136:137].strip(),
                set_aside_pct=int(pct) if pct.isdigit() else 0,
            )
        )
    return out


def parse_rfq_dates_page(html: str) -> list[tuple[str, date, str]]:
    """Daily file links on RfqDates.aspx: [(kind 'in'|'bq', post date, url)]."""
    out = []
    for m in re.finditer(r"href=[\"']?([^\"' >]+/Archive/(in|bq)(\d{6})\.(?:txt|zip))", html, re.I):
        url, kind, ymd = m.group(1), m.group(2).lower(), m.group(3)
        d = _mdy(ymd, "%y%m%d")
        if d:
            out.append((kind, d, url))
    seen, uniq = set(), []
    for item in out:
        if item[:2] not in seen:
            seen.add(item[:2])
            uniq.append(item)
    return sorted(uniq, key=lambda t: (t[1], t[0]))


@dataclass
class BqRow:
    solicitation_number: str
    type_indicator: str  # F fast auto-eval, P auto-eval, I AIDC, '' manual
    set_aside_code: str
    return_by_date: date | None
    fob: str | None  # ORIGIN | DESTINATION
    inspection_point: str | None
    clin: str
    purchase_request: str
    item: str
    unit_of_issue: str
    quantity: float
    delivery_days: int | None
    aidc_terms: dict
    item_description_indicator: str
    higher_level_quality: str
    hubzone_preference: bool


@dataclass
class ApprovedSourceRow:
    nsn: str
    cage_code: str
    part_number: str
    company_name: str


_OD = {"O": "ORIGIN", "D": "DESTINATION"}


def _col(row: list[str], n: int) -> str:
    """1-based column access as in BatchFormat.aspx."""
    return row[n - 1].strip() if len(row) >= n else ""


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse_bq_text(text: str) -> list[BqRow]:
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 51 or not row[0].strip():
            continue
        dd = _col(row, 51)
        rows.append(
            BqRow(
                solicitation_number=_col(row, 1),
                type_indicator=_col(row, 2),
                set_aside_code=_col(row, 3),
                return_by_date=_mdy(_col(row, 5), "%m/%d/%Y"),
                fob=_OD.get(_col(row, 32)),
                inspection_point=_OD.get(_col(row, 36)),
                clin=_col(row, 44),
                purchase_request=_col(row, 46),
                item=_col(row, 47),
                unit_of_issue=_col(row, 48),
                quantity=_num(_col(row, 49)) or 0.0,
                delivery_days=int(dd) if dd.isdigit() else None,
                aidc_terms={
                    k: _num(_col(row, c))
                    for k, c in (("guaranteed_minimum", 52), ("do_minimum", 53), ("contract_maximum", 54),
                                 ("annual_frequency", 55))
                    if _num(_col(row, c)) is not None
                },
                item_description_indicator=_col(row, 105),
                higher_level_quality=_col(row, 117),
                hubzone_preference=_col(row, 57) == "Y",
            )
        )
    return rows


def parse_as_text(text: str) -> list[ApprovedSourceRow]:
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or not row[0].strip():
            continue
        out.append(
            ApprovedSourceRow(
                nsn=row[0].strip(),
                cage_code=row[1].strip().upper(),
                part_number=row[2].strip(),
                company_name=row[3].strip() if len(row) > 3 else "",
            )
        )
    return out


def parse_bq_zip(data: bytes) -> tuple[list[BqRow], list[ApprovedSourceRow]]:
    bq: list[BqRow] = []
    approved: list[ApprovedSourceRow] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            text = zf.read(name).decode("latin-1")
            base = name.lower().rsplit("/", 1)[-1]
            if base.startswith("bq"):
                bq.extend(parse_bq_text(text))
            elif base.startswith("as"):
                approved.extend(parse_as_text(text))
    return bq, approved
