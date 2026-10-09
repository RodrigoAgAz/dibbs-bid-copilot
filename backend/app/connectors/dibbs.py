"""DIBBS data sources: live (public site), fixture (offline samples), Apify (fallback).

All sources expose the same methods, returning raw bytes (or parsed records for index lines);
parsing lives in app/connectors/parsers. Live responses are cached in data/raw/dibbs/<date>/.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from app.config import get_settings
from app.connectors.base import PoliteHttp
from app.connectors.dibbs_session import DIBBS2, WWW, DibbsSession
from app.connectors.parsers.dibbs_files import IndexLine, parse_index, parse_rfq_dates_page, rfq_pdf_url
from app.connectors.parsers.dibbs_html import hidden_fields, parse_award_listing

log = logging.getLogger(__name__)

AWARD_GRID = "ctl00$cph1$grdAwardSearch"
MAX_AWARD_PAGES = 100


class DibbsSource:
    mode = "base"

    def rfq_index_dates(self) -> list[date]:
        raise NotImplementedError

    def rfq_index(self, d: date) -> bytes | None:
        raise NotImplementedError

    def index_lines(self, d: date) -> list[IndexLine]:
        data = self.rfq_index(d)
        return parse_index(data) if data else []

    def batch_quote(self, d: date) -> bytes | None:
        raise NotImplementedError

    def record_page(self, sol: str) -> bytes | None:
        raise NotImplementedError

    def rfq_pdf(self, sol: str) -> bytes | None:
        raise NotImplementedError

    def nsn_page(self, nsn: str) -> bytes | None:
        raise NotImplementedError

    def award_search(self, category: str, values: list[str], scope: str = "recent",
                     max_pages: int = MAX_AWARD_PAGES) -> list[bytes]:
        raise NotImplementedError

    def awards_by_post_date(self, d: date, max_pages: int = MAX_AWARD_PAGES) -> list[bytes]:
        raise NotImplementedError

    def award_dates(self) -> list[date]:
        raise NotImplementedError

    def award_pdf(self, url: str) -> bytes | None:
        raise NotImplementedError

    def cage_page(self, cage: str) -> bytes | None:
        raise NotImplementedError


# ----------------------------------------------------------------------------- live
class LiveDibbsSource(DibbsSource):
    mode = "live"

    def __init__(self, http: PoliteHttp | None = None, banner_mode: str | None = None) -> None:
        self.http = http or PoliteHttp("dibbs")
        self.session = DibbsSession(self.http, banner_mode or get_settings().dibbs_banner_mode)
        self._dates_html: str | None = None

    def _get(self, url: str, cache_name: str | None = None, method: str = "GET", data: dict | None = None) -> bytes:
        body = self.session.fetch(url, method=method, data=data)
        if cache_name:
            self.http.save_raw(cache_name, body)
        return body

    def _dates(self) -> list[tuple[str, date, str]]:
        if self._dates_html is None:
            self._dates_html = self._get(f"{WWW}/RFQ/RfqDates.aspx?category=recent", "rfqdates_recent.html").decode(
                "latin-1"
            )
        return parse_rfq_dates_page(self._dates_html)

    def rfq_index_dates(self) -> list[date]:
        return sorted({d for kind, d, _ in self._dates() if kind == "in"})

    def _file(self, kind: str, d: date) -> bytes | None:
        name = f"{kind}{d:%y%m%d}.{'txt' if kind == 'in' else 'zip'}"
        try:
            return self._get(f"{DIBBS2}/Downloads/RFQ/Archive/{name}", name)
        except FileNotFoundError:
            return None

    def rfq_index(self, d: date) -> bytes | None:
        return self._file("in", d)

    def batch_quote(self, d: date) -> bytes | None:
        return self._file("bq", d)

    def record_page(self, sol: str) -> bytes | None:
        try:
            return self._get(f"{WWW}/rfq/rfqrec.aspx?sn={sol}", f"rfqrec_{sol}.html")
        except FileNotFoundError:
            return None

    def rfq_pdf(self, sol: str) -> bytes | None:
        try:
            return self._get(rfq_pdf_url(sol), f"{sol}.pdf")
        except FileNotFoundError:
            return None

    def nsn_page(self, nsn: str) -> bytes | None:
        return self._get(f"{WWW}/RFQ/RFQNsn.aspx?value={nsn}&category=&Scope=", f"rfqnsn_{nsn}.html")

    def _paged(self, url: str, first: bytes, max_pages: int, cache_prefix: str) -> list[bytes]:
        pages = [first]
        listing = parse_award_listing(first)
        seen = {"Page$1"}
        n = 1
        while n < max_pages:
            nxt = f"Page${n + 1}"
            if nxt not in listing.pages or nxt in seen:
                break
            fields = {**hidden_fields(pages[-1]), "__EVENTTARGET": AWARD_GRID, "__EVENTARGUMENT": nxt}
            body = self._get(url, f"{cache_prefix}_p{n + 1}.html", method="POST", data=fields)
            pages.append(body)
            seen.add(nxt)
            listing = parse_award_listing(body)
            n += 1
        return pages

    def award_search(self, category: str, values: list[str], scope: str = "recent",
                     max_pages: int = MAX_AWARD_PAGES) -> list[bytes]:
        out: list[bytes] = []
        for i in range(0, len(values), 40):
            chunk = ",".join(values[i : i + 40])
            url = f"{WWW}/Awards/AwdRecs.aspx?Category={category}&TypeSrch=cq&Value={quote(chunk)}&Scope={scope}"
            first = self._get(url, f"awd_{category}_{scope}_{i}.html")
            out.extend(self._paged(url, first, max_pages, f"awd_{category}_{scope}_{i}"))
        return out

    def award_dates(self) -> list[date]:
        html = self._get(f"{WWW}/Awards/AwdDates.aspx?category=post", "awddates_post.html").decode("latin-1")
        out = set()
        for m in re.finditer(r"Value=(\d\d)-(\d\d)-(\d{4})", html):
            out.add(date(int(m.group(3)), int(m.group(1)), int(m.group(2))))
        return sorted(out)

    def awards_by_post_date(self, d: date, max_pages: int = MAX_AWARD_PAGES) -> list[bytes]:
        url = f"{WWW}/Awards/AwdRecs.aspx?Category=post&TypeSrch=cq&Value={d:%m-%d-%Y}"
        first = self._get(url, f"awdrecs_post_{d:%m-%d-%Y}_p1.html")
        return self._paged(url, first, max_pages, f"awdrecs_post_{d:%m-%d-%Y}")

    def award_pdf(self, url: str) -> bytes | None:
        try:
            return self._get(url, url.rsplit("/", 1)[-1])
        except FileNotFoundError:
            return None

    def cage_page(self, cage: str) -> bytes | None:
        return self._get(f"{WWW}/Refs/cage.aspx?Cage={cage}", f"cage_{cage}.html")


# ----------------------------------------------------------------------------- fixtures
class FixtureDibbsSource(DibbsSource):
    """Serves the real samples captured in backend/tests/fixtures (offline demo/test mode)."""

    mode = "fixture"

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or get_settings().fixtures_dir)
        self.d = self.root / "dibbs"

    def _read(self, path: Path) -> bytes | None:
        return path.read_bytes() if path.exists() else None

    def rfq_index_dates(self) -> list[date]:
        out = []
        for p in (self.d / "rfq_index").glob("in*.txt"):
            m = re.match(r"in(\d\d)(\d\d)(\d\d)\.txt", p.name)
            if m:
                out.append(date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))))
        return sorted(out)

    def rfq_index(self, d: date) -> bytes | None:
        return self._read(self.d / "rfq_index" / f"in{d:%y%m%d}.txt")

    def batch_quote(self, d: date) -> bytes | None:
        return self._read(self.d / "rfq_index" / f"bq{d:%y%m%d}.zip")

    def record_page(self, sol: str) -> bytes | None:
        return self._read(self.d / "rfq_record" / f"rfqrec_{sol}.html")

    def rfq_pdf(self, sol: str) -> bytes | None:
        return self._read(self.d / "rfq_pdf" / f"{sol}.pdf")

    def nsn_page(self, nsn: str) -> bytes | None:
        return self._read(self.d / "nsn" / f"rfqnsn_{nsn}.html")

    def award_search(self, category: str, values: list[str], scope: str = "recent",
                     max_pages: int = MAX_AWARD_PAGES) -> list[bytes]:
        # The fixture NSN searches cover whatever NSNs were captured; callers filter rows by value.
        if category != "nsn":
            return []
        return [p.read_bytes() for p in sorted((self.d / "awards").glob("awdrecs_nsn_search_*.html"))]

    def award_dates(self) -> list[date]:
        out = []
        for p in (self.d / "awards").glob("awdrecs_post_*_page1.html"):
            m = re.search(r"(\d\d)-(\d\d)-(\d{4})", p.name)
            if m:
                out.append(date(int(m.group(3)), int(m.group(1)), int(m.group(2))))
        return sorted(out)

    def awards_by_post_date(self, d: date, max_pages: int = MAX_AWARD_PAGES) -> list[bytes]:
        return [p.read_bytes() for p in sorted((self.d / "awards").glob(f"awdrecs_post_{d:%m-%d-%Y}_page*.html"))][:max_pages]

    def award_pdf(self, url: str) -> bytes | None:
        return self._read(self.d / "award_pdf" / url.rsplit("/", 1)[-1].replace(".PDF", ".pdf"))

    def cage_page(self, cage: str) -> bytes | None:
        return self._read(self.root / "cage" / f"dibbs_cage_{cage}.html")


def get_dibbs_source(mode: str | None = None) -> DibbsSource:
    mode = mode or get_settings().connector_mode
    if mode == "live":
        return LiveDibbsSource()
    if mode == "apify":
        from app.connectors.apify import ApifyDibbsSource

        return ApifyDibbsSource()
    return FixtureDibbsSource()
