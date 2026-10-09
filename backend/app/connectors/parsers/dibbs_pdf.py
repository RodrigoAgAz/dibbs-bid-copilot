"""Parsers for DIBBS PDFs: RFQ solicitations (SF-18) and award documents (DD 1155).

Text comes from pdfplumber (fallback pypdf). Landmarks are documented in docs/data_sources.md
sections 6 (RFQ) and 8d (awards). Two RFQ dialects exist: automated (T/U) and manual (Q).
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime

log = logging.getLogger(__name__)

PAGE_MARK = "<<<PAGE {n} END>>>"


def pdf_to_text(data: bytes, max_pages: int = 120) -> str:
    try:
        import pdfplumber

        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        parts = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages[:max_pages], start=1):
                parts.append((page.extract_text() or "") + "\n" + PAGE_MARK.format(n=i))
        return "\n".join(parts)
    except Exception as exc:  # fall back to pypdf
        log.warning("pdfplumber failed (%s); falling back to pypdf", exc)
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(
            (p.extract_text() or "") + "\n" + PAGE_MARK.format(n=i) for i, p in enumerate(reader.pages[:max_pages], 1)
        )


_NOISE = [
    re.compile(r"^CONTINUATION SHEET REFERENCE NO\..*$"),
    re.compile(r"^SP\w{4}-\d\d-[A-Z]-\w{4}$"),
    re.compile(r"^SECTION [A-Z]$"),
    re.compile(r"^CONTINUED ON NEXT PAGE$"),
    re.compile(r"^<<<PAGE \d+ END>>>$"),
    re.compile(r"^PR: \d{10} PRLI: \d{4} CONT.D$"),
    re.compile(r"^SUPPLY/SERVICE: .* CONT'D$"),
]


def clean_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or any(p.match(line) for p in _NOISE):
            continue
        out.append(line)
    return out


def _pdf_date(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip().title(), "%Y %b %d").date()
    except ValueError:
        return None


def _num(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class HistoryRow:
    nsn: str
    awardee_cage: str
    contract_number: str
    quantity: float
    unit_price: float
    award_date: date
    surplus: bool


@dataclass
class RfqClin:
    clin: str
    purchase_request: str | None
    nsn: str | None
    unit_of_issue: str
    quantity: float
    delivery_days: int | None = None
    fob: str | None = None
    inspection_point: str | None = None
    packaging: str | None = None
    first_article: bool = False


@dataclass
class RfqPdf:
    solicitation_number: str | None = None
    issue_date: date | None = None
    return_by_date: date | None = None
    header_delivery_days: int | None = None
    buyer_name: str | None = None
    buyer_code: str | None = None
    buyer_phone: str | None = None
    buyer_email: str | None = None
    naics: str | None = None
    fast_award: bool = False
    aidc: bool = False
    aidc_terms: dict = field(default_factory=dict)
    set_aside_notice: str | None = None
    first_article: bool = False
    inspection_point: str | None = None
    fob: str | None = None
    packaging: str | None = None
    clins: list[RfqClin] = field(default_factory=list)
    approved_sources: list[tuple[str, str, str]] = field(default_factory=list)  # (cage, part number, company)
    history: list[HistoryRow] = field(default_factory=list)
    no_history: bool = False


HDR_RE = re.compile(r"^(SP\w{4}-\d\d-[TUQ]-\w{4}) (\d{4} [A-Z]{3} \d\d) (\S+)")
BUYER_TU = re.compile(r"^Name: (.+?) Buyer Code:\s*(\w+) Tel: (.+?)(?: Fax: .+)?$")
BUYER_Q = re.compile(r"^Buyer: (.+) ([A-Z0-9]{7}) Tel: (.+)$")
EMAIL_RE = re.compile(r"^Email: (\S+@\S+)")
NAICS_RE = re.compile(r"NORTH AMERICAN INDUSTRY CLASSIFICATION SYSTEM (\d{6})")
NAICS_FALLBACK = re.compile(r"NAICS\) code for this acquisition is (\d{6})")
HIST_HDR = re.compile(r"^Procurement History for NSN/FSC:\s*(\w{9})/(\d{4})")
HIST_ROW = re.compile(r"^([0-9A-Z]{5}) (\S+) ([\d,]+\.\d{3}) ([\d,]+\.\d{5}) (\d{8}) ([YN])$")
CLIN_TU = re.compile(r"^(\d{4}) (\d{10}) (\d{4}) ([A-Z]{2}) ([\d,]+\.\d{3}) \.$")
CLIN_Q = re.compile(r"^(\d{4}) ([\w-]+(?: - \w+)?) ([\d,]+\.\d{3}) ([A-Z]{2}) \$")
SOURCE_RE = re.compile(r"^(.+?) ([0-9A-Z]{5}) P/N (\S.*)$")
AIDC_TERMS = {
    "guaranteed_minimum_qty": re.compile(r"Guaranteed Contract Minimum Quantity:\s*([\d,.]+)"),
    "contract_maximum_value": re.compile(r"Contract Maximum Value:\s*\$([\d,.]+)"),
    "estimated_annual_qty": re.compile(r"Estimated Annual Quantity:\s*([\d,.]+)"),
    "minimum_order_qty": re.compile(r"Minimum Delivery Order Quantity:\s*([\d,.]+)"),
}
SET_ASIDE_NOTICES = [
    ("52.219-30", "WOSB"),
    ("52.219-29", "EDWOSB"),
    ("52.219-27", "SDVOSB"),
    ("52.219-3 ", "HUBZONE"),
    ("52.219-6", "SB"),
]


def parse_rfq_pdf_text(text: str) -> RfqPdf:
    lines = clean_lines(text)
    full = "\n".join(lines)
    out = RfqPdf()
    for i, line in enumerate(lines):
        if out.solicitation_number is None and (m := HDR_RE.match(line)):
            out.solicitation_number = m.group(1).replace("-", "")
            out.issue_date = _pdf_date(m.group(2))
        if out.return_by_date is None and line.startswith("be completed by the quoter"):
            for nxt in lines[i + 1 : i + 4]:
                if re.fullmatch(r"\d{4} [A-Z]{3} \d\d", nxt):
                    out.return_by_date = _pdf_date(nxt)
                    break
        if out.buyer_name is None:
            m = BUYER_TU.match(line) or BUYER_Q.match(line)
            if m:
                out.buyer_name, out.buyer_code, out.buyer_phone = m.group(1).strip(), m.group(2), m.group(3).strip()
        if out.buyer_email is None and (m := EMAIL_RE.match(line)):
            out.buyer_email = m.group(1).lower()
    if m := re.search(r"(\d+) DAYS ADO", full):
        out.header_delivery_days = int(m.group(1))
    m = NAICS_RE.search(full) or NAICS_FALLBACK.search(full)
    out.naics = m.group(1) if m else None
    out.fast_award = "MICRO-PURCHASE QUOTES MAY BE AWARDED PRIOR" in full
    out.aidc = "Indefinite Delivery Contract (IDC)" in full or "AUTOMATED INDEFINITE DELIVERY" in full.upper()
    for k, pat in AIDC_TERMS.items():
        if m := pat.search(full):
            out.aidc_terms[k] = _num(m.group(1).rstrip("."))
    for clause, label in SET_ASIDE_NOTICES:
        if f"FAR {clause}" in full or f"FAR {clause.strip()} " in full:
            out.set_aside_notice = label
            break
    out.first_article = "FIRST ARTICLE TESTING IS REQUIRED" in full or "S00000052" in full

    # Procurement history blocks (may repeat across page breaks; merge by NSN, de-duplicate rows).
    current_nsn = None
    seen: set[tuple] = set()
    for line in lines:
        if m := HIST_HDR.match(line):
            current_nsn = f"{m.group(2)}{m.group(1)}"
            continue
        if line == "No History Available":
            out.no_history = True
            continue
        if current_nsn and (m := HIST_ROW.match(line)):
            d = datetime.strptime(m.group(5), "%Y%m%d").date()
            key = (current_nsn, m.group(2), d, m.group(3))
            if key in seen:
                continue
            seen.add(key)
            out.history.append(
                HistoryRow(current_nsn, m.group(1), m.group(2), _num(m.group(3)), _num(m.group(4)), d, m.group(6) == "Y")
            )

    # Approved sources (company, CAGE, P/N).
    for line in lines:
        if (m := SOURCE_RE.match(line)) and "CAGE:" not in line and not line.startswith(("Administrative", "MFR.")):
            src = (m.group(2), m.group(3).strip(), m.group(1).strip())
            if src not in out.approved_sources:
                out.approved_sources.append(src)

    # CLINs and their attributes.
    cur: RfqClin | None = None
    in_pack = False
    for line in lines:
        m_tu, m_q = CLIN_TU.match(line), CLIN_Q.match(line)
        if m_tu:
            cur = RfqClin(m_tu.group(1), m_tu.group(2), None, m_tu.group(4), _num(m_tu.group(5)))
            out.clins.append(cur)
            in_pack = False
            continue
        if m_q:
            item = m_q.group(2)
            nsn = item.replace("-", "") if re.fullmatch(r"\d{4}-\d{2}-\d{3}-\d{4}", item) else None
            cur = RfqClin(m_q.group(1), None, nsn, m_q.group(4), _num(m_q.group(3)),
                          first_article=bool(re.search(r"S0000005[0-9]", item)))
            out.clins.append(cur)
            in_pack = False
            continue
        if cur is None:
            continue
        if line.startswith("NSN/MATERIAL:"):
            cur.nsn = line.split(":", 1)[1].strip()
        elif m := re.match(r"DELIVERY \(IN DAYS\):(\d+)", line):
            cur.delivery_days = int(m.group(1))
        elif m := re.match(r"(?:DELIVER )?FOB: (ORIGIN|DESTINATION)(?: DELIVERY DATE: (\d+) DAYS ADO)?", line):
            cur.fob = m.group(1)
            if m.group(2):
                cur.delivery_days = int(m.group(2))
        elif m := re.match(r"INSPECTION POINT: (ORIGIN|DESTINATION)", line):
            cur.inspection_point = m.group(1)
        elif line.startswith("PREP FOR DELIVERY"):
            in_pack = True
        elif in_pack:
            if "ASTM D3951" in line:
                cur.packaging = "Commercial packaging per ASTM D3951; marking per MIL-STD-129"
                in_pack = False
            elif "MIL-STD-2073" in line:
                cur.packaging = "Military packaging per MIL-STD-2073-1E"
            elif cur.packaging and line.startswith("QUP:"):
                cur.packaging += f" ({line})"
            elif line.startswith("MARKING SHALL"):
                if cur.packaging and "MIL-STD-129" not in cur.packaging:
                    cur.packaging += "; marking per MIL-STD-129"
                in_pack = False
    real = [c for c in out.clins if not c.first_article]
    if real:
        out.inspection_point = next((c.inspection_point for c in real if c.inspection_point), None)
        out.fob = next((c.fob for c in real if c.fob), None)
        out.packaging = next((c.packaging for c in real if c.packaging), None)
    return out


# --------------------------------------------------------------------------- award PDFs
@dataclass
class AwardLine:
    clin: str
    item: str | None  # NSN (no hyphens) / PR / basic CLIN ref, depending on the format
    quantity: float
    unit_of_issue: str
    unit_price: float
    total: float
    fmt: str  # P | V | F


@dataclass
class AwardPdf:
    contractor_cage: str | None
    total: float | None
    lines: list[AwardLine]
    nsns: list[str]


AW_P = re.compile(r"^(\d{4}) ([\d-]{16}|\S+) ([\d,]+\.\d{3}) ([A-Z]{2}) \$ ([\d,.]+) \$ ([\d,.]+) \.$")
AW_V = re.compile(r"^(\d{4}) (\d{10}) (\d{4}) ([A-Z]{2}) ([\d,]+\.\d{3}) ([\d,]+\.\d+) USD ([\d,.]+) \.$")
AW_F = re.compile(r"^(\d{4}) (\d{4}) ([\d,]+\.\d{3}) ([A-Z]{2}) ([\d,]+\.\d+) ([\d,.]+) \d{4} [A-Z]{3} \d\d \.$")


def parse_award_pdf_text(text: str) -> AwardPdf:
    lines = clean_lines(text)
    full = "\n".join(lines)
    cage = m.group(1) if (m := re.search(r"CONTRACTOR CODE ([0-9A-Z]{5})", full)) else None
    total = _num(m.group(1)) if (m := re.search(r"25\. TOTAL \$([\d,.]+)", full)) else None
    out: list[AwardLine] = []
    for line in lines:
        if m := AW_V.match(line):
            out.append(AwardLine(m.group(1), m.group(2), _num(m.group(5)), m.group(4), _num(m.group(6)), _num(m.group(7)), "V"))
        elif m := AW_F.match(line):
            out.append(AwardLine(m.group(1), m.group(2), _num(m.group(3)), m.group(4), _num(m.group(5)), _num(m.group(6)), "F"))
        elif m := AW_P.match(line):
            item = m.group(2).replace("-", "") if re.fullmatch(r"\d{4}-\d{2}-\d{3}-\d{4}", m.group(2)) else m.group(2)
            out.append(AwardLine(m.group(1), item, _num(m.group(3)), m.group(4), _num(m.group(5)), _num(m.group(6)), "P"))
    nsns = sorted({x.split(":", 1)[1].strip() for x in lines if x.startswith("NSN/MATERIAL:")})
    return AwardPdf(cage, total, out, nsns)
