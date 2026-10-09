"""Parsers for DIBBS HTML pages: RFQ record (package view), NSN page, award listing/package, CAGE.

See docs/data_sources.md sections 5, 7, 8 and 9 for the observed structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from bs4 import BeautifulSoup

BANNER_MARKERS = ("butAgree", "Warning and Consent")


def is_banner(html: str | bytes) -> bool:
    s = html.decode("latin-1", errors="replace") if isinstance(html, bytes) else html
    return all(m in s[:20000] for m in BANNER_MARKERS)


def _soup(html: str | bytes) -> BeautifulSoup:
    if isinstance(html, bytes):
        html = html.decode("latin-1", errors="replace")
    return BeautifulSoup(html, "lxml")


def _text(el) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ") if el is not None else ""


def _mdy(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%m-%d-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _money(s: str) -> float | None:
    m = re.search(r"\$?\s*([\d,]+\.?\d*)", s or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def hidden_fields(html: str | bytes) -> dict[str, str]:
    """All hidden inputs (ASP.NET viewstate etc.) for postbacks."""
    soup = _soup(html)
    return {i.get("name"): i.get("value", "") for i in soup.find_all("input", attrs={"type": "hidden"}) if i.get("name")}


# --------------------------------------------------------------------------- RFQ record page
@dataclass
class RecordItem:
    line_no: int
    item: str  # NSN without hyphens (or part number)
    nomenclature: str
    purchase_request: str
    quantity: float | None
    tech_docs: bool


@dataclass
class RecordAward:
    awardee_cage: str
    contract_number: str
    award_date: date | None
    total_value: float | None


@dataclass
class RecordPage:
    solicitation_number: str
    status: str
    issue_date: date | None
    return_by_date: date | None
    fast_award: bool
    aidc: bool
    set_aside_label: str | None
    pdf_url: str | None
    items: list[RecordItem] = field(default_factory=list)
    award: RecordAward | None = None

    @property
    def tech_docs(self) -> bool:
        return any(i.tech_docs for i in self.items)


AWARD_RE = re.compile(
    r"Awardee:\s*([0-9A-Z]{5}).*?Order:\s*([0-9A-Z]+).*?Date:\s*([\d-]+).*?Total value:\s*\$([\d,.]+)", re.S
)


def parse_record_page(html: str | bytes) -> RecordPage:
    soup = _soup(html)
    head = soup.find(id="ctl00_cph1_lblSolicitation")
    alts = [img.get("alt", "") for img in head.find_all("img")] if head else []
    set_aside = next((a for a in alts if "Set" in a and "Aside" in a or "Set-Aside" in a), None)
    status = _text(soup.find(id="ctl00_cph1_lblRfqStatus")).replace(" uote", "").strip()
    docs = soup.find(id="RfqDocsDisplay")
    pdf = docs.find("a", href=re.compile(r"\.PDF$", re.I)) if docs else None
    page = RecordPage(
        solicitation_number=_text(head.find("strong") if head and head.find("strong") else head).replace("-", ""),
        status=status.split()[0] if status else "",
        issue_date=_mdy(_text(soup.find(id="ctl00_cph1_lblIssue"))),
        return_by_date=_mdy(_text(soup.find(id="ctl00_cph1_lblReturnBy"))),
        fast_award=any(a.startswith("Fast Award") for a in alts),
        aidc=any("AIDC" in a or "Indefinite Delivery" in a for a in alts),
        set_aside_label=set_aside,
        pdf_url=pdf["href"] if pdf else None,
    )
    table = soup.find("table", attrs={"summary": re.compile("RFQ PR data")})
    if table:
        for tr in table.find_all("tr"):
            num = tr.find("td", attrs={"headers": "xrh0"})
            if num is not None:
                cells = {h: tr.find("td", attrs={"headers": h}) for h in ("xrh1", "xrh2", "xrh3", "xrh4")}
                prq = _text(cells["xrh4"])
                m = re.match(r"(\S+)\s*Qty:\s*([\d,]+)", prq)
                page.items.append(
                    RecordItem(
                        line_no=int(_text(num) or 0),
                        item=_text(cells["xrh1"]).replace("-", ""),
                        nomenclature=_text(cells["xrh2"]),
                        purchase_request=m.group(1) if m else prq.split(" ")[0],
                        quantity=float(m.group(2).replace(",", "")) if m else None,
                        tech_docs="Tech Docs" in _text(cells["xrh3"]),
                    )
                )
            elif "Awarded" in _text(tr):
                m = AWARD_RE.search(_text(tr))
                if m:
                    page.award = RecordAward(m.group(1), m.group(2), _mdy(m.group(3)), _money(m.group(4)))
    return page


# --------------------------------------------------------------------------- NSN page
@dataclass
class NsnPage:
    nsn: str
    nomenclature: str | None
    amsc: str | None
    approved_sources: list[tuple[str, str, str]]  # (cage, part number, company)


def parse_nsn_page(html: str | bytes) -> NsnPage:
    soup = _soup(html)
    txt = soup.get_text(" ", strip=True).replace("\xa0", " ")
    nsn = re.search(r"NSN:\s*([\w-]+)", txt)
    nom = re.search(r"Nomenclature:\s*(.+?)\s+AMSC:", txt)
    amsc = re.search(r"AMSC:\s*([A-Z0-9])\b", txt)
    sources = []
    t = soup.find("table", attrs={"summary": re.compile("Approved Source", re.I)})
    if t:
        for tr in t.find_all("tr"):
            tds = [_text(td) for td in tr.find_all("td")]
            if len(tds) >= 2 and re.fullmatch(r"[0-9A-Z]{5}", tds[0]):
                sources.append((tds[0], tds[1], tds[2] if len(tds) > 2 else ""))
    return NsnPage(
        nsn=nsn.group(1).replace("-", "") if nsn else "",
        nomenclature=nom.group(1).strip() if nom else None,
        amsc=amsc.group(1) if amsc else None,
        approved_sources=sources,
    )


# --------------------------------------------------------------------------- awards
@dataclass
class AwardListingRow:
    award_basic: str
    delivery_order: str
    delivery_order_counter: str
    awardee_cage: str
    total_price: float | None
    award_date: date | None
    posted_date: date | None
    nsn: str
    nomenclature: str
    purchase_request: str
    solicitation_number: str

    @property
    def contract_number(self) -> str:
        return self.delivery_order or self.award_basic

    @property
    def pdf_url(self) -> str | None:
        if not self.posted_date:
            return None
        folder = self.posted_date.strftime("%d%b%y").upper()
        return f"https://dibbs2.bsm.dla.mil/Downloads/Awards/{folder}/{self.award_basic}{self.delivery_order}.PDF"


@dataclass
class AwardListing:
    records_found: int
    rows: list[AwardListingRow]
    pages: list[str]  # postback arguments for other pages (e.g. 'Page$2')
    hidden: dict[str, str]


def parse_award_listing(html: str | bytes) -> AwardListing:
    soup = _soup(html)
    txt = soup.get_text(" ", strip=True)
    m = re.search(r"Records Found:?\s*([\d,]+)", txt)
    rows: list[AwardListingRow] = []
    grid = soup.find(id="ctl00_cph1_grdAwardSearch")
    pages: list[str] = []
    if grid:
        for tr in grid.find_all("tr"):
            spans = {sp.get("id", "").rsplit("_", 1)[-1]: sp for sp in tr.find_all("span") if sp.get("id")}
            if "lblAwardBasicNumber" not in spans:
                for a in tr.find_all("a", href=re.compile(r"Page\$")):
                    arg = re.search(r"'(Page\$[^']+)'", a["href"])
                    if arg and arg.group(1) not in pages:
                        pages.append(arg.group(1))
                continue

            def g(key: str, spans=spans) -> str:
                return _text(spans.get(key))

            rows.append(
                AwardListingRow(
                    award_basic=g("lblAwardBasicNumber").split(" ")[0],
                    delivery_order=g("lblDeliveryOrder").split(" ")[0],
                    delivery_order_counter=g("lblDeliveryOrderCounter"),
                    awardee_cage=g("lblCage"),
                    total_price=None if "See" in g("lblTotalContactPrice") else _money(g("lblTotalContactPrice")),
                    award_date=_mdy(g("lblAwardDate")),
                    posted_date=_mdy(g("lblPostedDate")),
                    nsn=g("lblNsn").replace("-", ""),
                    nomenclature=g("lblNomenclature"),
                    purchase_request=g("lblPurchaseRequest"),
                    solicitation_number=g("lblSolicitation").replace("-", ""),
                )
            )
    return AwardListing(int(m.group(1).replace(",", "")) if m else len(rows), rows, pages, hidden_fields(html))


@dataclass
class AwardPackage:
    award_basic: str
    delivery_order: str
    award_date: date | None
    price: float | None
    awardee_cage: str
    awardee_name: str
    posted_date: date | None


def parse_award_package(html: str | bytes) -> AwardPackage:
    soup = _soup(html)

    def g(i: str) -> str:
        return _text(soup.find(id=f"ctl00_cph1_{i}"))

    return AwardPackage(
        award_basic=g("lblAwardBasic"),
        delivery_order=g("lblDeliveryOrder"),
        award_date=_mdy(g("lblAwarded")),
        price=_money(g("lblPrice")),
        awardee_cage=g("lblCageHyperlink"),
        awardee_name=g("lblAwardee"),
        posted_date=_mdy(g("lblPosted")),
    )


# --------------------------------------------------------------------------- CAGE
@dataclass
class CageInfo:
    cage_code: str
    name: str | None
    address: str | None
    city: str | None
    state: str | None
    country: str | None
    zip: str | None


def parse_dibbs_cage(html: str | bytes) -> CageInfo | None:
    soup = _soup(html)

    def g(i: str) -> str | None:
        v = _text(soup.find(id=i))
        return v or None

    code = g("lblCageCode")
    if not code:
        return None
    addr = " ".join(x for x in (g("lblVendorStreetAddress"), g("lblVendorStreetAddress1")) if x) or None
    return CageInfo(code, g("lblVendorName"), addr, g("lblVendorCity"), g("lblVendorState"), g("lblVendorCountry"),
                    g("lblVendorZip"))
