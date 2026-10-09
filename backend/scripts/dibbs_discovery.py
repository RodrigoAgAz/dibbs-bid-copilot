#!/usr/bin/env python
"""DIBBS public-data discovery script.

Captures REAL samples of public DLA DIBBS data (plus CAGE and USAspending
samples) into ``backend/tests/fixtures`` so that parsers can be written and
tested against them.  This is a *discovery* tool, not the production scraper.

What it fetches (see ``docs/data_sources.md`` for the findings):

  A. RFQ dates pages (recent / issue / close), file-format help pages
  B. Daily RFQ index files ``inYYMMDD.txt`` and batch-quote zips ``bqYYMMDD.zip``
     (+ statistics by solicitation type and FSC)
  C. RFQ record ("Package View") pages ``/rfq/rfqrec.aspx?sn=...``
  D. RFQ solicitation PDFs (+ pdfplumber text extraction)
  E. Public RFQ NSN pages ``/RFQ/RFQNsn.aspx?value=<NSN>``
  F. Awards: search page, award dates pages, award listings by post date
     (incl. a postback-paged page 2), multi-NSN award searches, award package
     views and award PDFs (+ text)
  G. CAGE: DIBBS' own public CAGE pop-up (``/Refs/cage.aspx``) and a probe of
     cage.dla.mil (records the usage-agreement gate; never accepts it)
  H. USAspending ``spending_by_award`` (+ one award detail) JSON

Access mechanism
----------------
Every DIBBS page (www.dibbs.bsm.dla.mil and the file host dibbs2.bsm.dla.mil)
is gated by a DoD Notice and Consent banner (``/dodwarning.aspx?goto=...``).
As specified by the project owner, the banner is accepted ("OK" button) once
per host, either with headless Chromium (``--banner browser``, default) or by
replaying the ASP.NET form POST without a browser (``--banner form``).  The
resulting session cookies (notably ``dw``) are loaded into an httpx client for
the bulk downloads.  No login is ever performed, nothing behind a DIBBS login
(JCP/cFolders/TDMT technical data, quote forms) is accessed, and no agreement
on any other site is accepted.

Politeness: <= 1 request/second/host, exponential backoff on 429/5xx/network
errors, and the project User-Agent on every request (httpx and Playwright).

Usage (from the repo root)::

    backend/.venv/Scripts/python backend/scripts/dibbs_discovery.py --help
    backend/.venv/Scripts/python backend/scripts/dibbs_discovery.py            # everything
    backend/.venv/Scripts/python backend/scripts/dibbs_discovery.py --steps rfq_dates,rfq_index
    backend/.venv/Scripts/python backend/scripts/dibbs_discovery.py --banner form --dates 261008,261007
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import io
import json
import logging
import random
import re
import sys
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "DIBBSBidCopilot/0.1 (+mailto:ops@copilot.example.com)"

WWW = "https://www.dibbs.bsm.dla.mil"
FILES = "https://dibbs2.bsm.dla.mil"
RFQ_ARCHIVE = f"{FILES}/Downloads/RFQ/Archive"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures"
DEFAULT_RAW = REPO_ROOT / "data" / "raw" / "dibbs_discovery"

DEFAULT_FSCS = ["5305", "5306", "5310", "5330", "5340", "4730", "6145"]

# Hand-picked sample solicitations (all from the 2026-10-02..10-08 index files)
# covering: T (automated) / U (AIDC) / Q (manual) types, Fast Award (F),
# Auto-evaluation (P), AIDC (I), set-asides, multi-line, part-number items.
DEFAULT_SOLICITATIONS = [
    "SPE4A027T0162",  # 5310, T/F fast award, 18 lines same NSN, COTS source
    "SPE4A627T0486",  # 5305, T/P auto-eval, WOSB set-aside
    "SPE7L127T0501",  # 5330, T/P, small-business set-aside, 2 lines
    "SPE7M027T0003",  # 6145, T/F, QPL item
    "SPE7L127U0037",  # 5340, U = AIDC (I), SB set-aside, source-controlled
    "SPE7M127U0029",  # 4730, U = AIDC (I), SB set-aside
    "SPE4A627Q0061",  # 5305, Q manual, SB set-aside, 2 lines
    "SPE4A627Q0058",  # 5306, Q manual, source-controlled, AS9100
    "SPE7L127Q0024",  # 5340, Q manual
    "SPEFA327Q0003",  # 4730, Q manual, non-NSN part-number style item
]

# Index file fixed-width layout (from /Rfq/RfqFileDefs.aspx, verified on data)
INDEX_LAYOUT: list[tuple[str, int]] = [
    ("solicitation_number", 13),
    ("nsn_or_part_number", 46),
    ("purchase_request", 13),
    ("return_by_date", 8),
    ("file_name", 19),
    ("quantity", 7),
    ("unit_of_issue", 2),
    ("nomenclature", 21),
    ("buyer_code", 5),
    ("amsc", 1),
    ("item_type", 1),
    ("set_aside", 1),
    ("set_aside_pct", 3),
]
INDEX_RECORD_LEN = sum(w for _, w in INDEX_LAYOUT)  # 140

log = logging.getLogger("dibbs_discovery")


# --------------------------------------------------------------------------
# HTTP client with politeness
# --------------------------------------------------------------------------
class PoliteClient:
    """httpx wrapper: per-host rate limit, retry with exponential backoff."""

    def __init__(self, min_interval: float = 1.1, max_retries: int = 4, timeout: float = 120.0):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
            timeout=timeout,
        )
        self._last: dict[str, float] = {}
        self.request_log: list[dict[str, Any]] = []

    def _wait(self, host: str) -> None:
        elapsed = time.monotonic() - self._last.get(host, 0.0)
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def request(self, method: str, url: str, *, follow: bool = True, **kw: Any) -> httpx.Response:
        """Perform a request. Redirects are followed manually (each hop is rate limited)."""
        hops = 0
        while True:
            resp = self._request_once(method, url, **kw)
            if follow and resp.status_code in (301, 302, 303, 307, 308) and hops < 5:
                url = urljoin(str(resp.url), resp.headers["location"])
                if resp.status_code in (301, 302, 303):
                    method, kw = "GET", {k: v for k, v in kw.items() if k not in ("data", "json", "content")}
                hops += 1
                continue
            return resp

    def _request_once(self, method: str, url: str, **kw: Any) -> httpx.Response:
        host = urlparse(url).netloc
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait(host)
            try:
                resp = self.client.request(method, url, **kw)
            except httpx.HTTPError as exc:  # network error -> backoff
                self._last[host] = time.monotonic()
                last_exc = exc
                log.warning("%s %s -> %s (attempt %d)", method, url, exc, attempt + 1)
            else:
                self._last[host] = time.monotonic()
                self.request_log.append(
                    {"method": method, "url": url, "status": resp.status_code,
                     "location": resp.headers.get("location"), "bytes": len(resp.content)}
                )
                log.info("%s %s -> %s (%d bytes)", method, url, resp.status_code, len(resp.content))
                if resp.status_code not in (429, 500, 502, 503, 504):
                    return resp
                last_exc = None
                if attempt == self.max_retries:
                    return resp
            time.sleep(delay + random.uniform(0, 0.5))
            delay *= 2
        assert last_exc is not None
        raise last_exc

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> httpx.Response:
        return self.request("POST", url, **kw)

    def cookie_summary(self) -> list[dict[str, str]]:
        return [{"name": c.name, "domain": c.domain, "path": c.path} for c in self.client.cookies.jar]


# --------------------------------------------------------------------------
# DoD Notice and Consent banner
# --------------------------------------------------------------------------
def is_banner(resp: httpx.Response) -> bool:
    if "dodwarning.aspx" in (resp.headers.get("location") or "").lower():
        return True
    ctype = resp.headers.get("content-type", "")
    return "text/html" in ctype and "butAgree" in resp.text and "Warning and Consent" in resp.text


def accept_banner_form(pc: PoliteClient, start_url: str) -> dict[str, Any]:
    """Accept the DoD banner without a browser by replaying the ASP.NET form POST.

    www host: GET target -> 302 to /dodwarning.aspx?goto=... -> 200 form.
    dibbs2 host: GET target -> 200 banner HTML served in place (no redirect).
    POST all hidden inputs (__VIEWSTATE*, __VIEWSTATEGENERATOR, __EVENTVALIDATION)
    plus ``butAgree=OK`` to the form action. Server sets cookie ``dw``.
    """
    trace: dict[str, Any] = {"mode": "form", "start_url": start_url}
    r = pc.request("GET", start_url, follow=False)
    trace["first_status"] = r.status_code
    trace["first_location"] = r.headers.get("location")
    warn_url = start_url
    if r.status_code in (301, 302):
        warn_url = urljoin(start_url, r.headers["location"])
        r = pc.request("GET", warn_url, follow=False)
    if not is_banner(r):
        trace["result"] = "no banner shown"
        return trace
    soup = BeautifulSoup(r.text, "lxml")
    form = soup.find("form", id="FrmBody") or soup.find("form")
    data = {
        i["name"]: i.get("value", "")
        for i in form.find_all("input")
        if i.get("type") == "hidden" and i.get("name")
    }
    trace["hidden_fields"] = sorted(data)
    data["butAgree"] = "OK"
    post_url = urljoin(warn_url, form.get("action"))
    trace["post_url"] = post_url
    r = pc.request("POST", post_url, data=data, follow=False)
    trace["post_status"] = r.status_code
    trace["post_location"] = r.headers.get("location")
    trace["cookies"] = pc.cookie_summary()
    return trace


def accept_banner_browser(pc: PoliteClient, start_urls: Iterable[str]) -> list[dict[str, Any]]:
    """Accept the banner with headless Chromium and copy cookies into httpx."""
    from playwright.sync_api import sync_playwright  # imported lazily

    traces = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, accept_downloads=False)
        page = ctx.new_page()
        for url in start_urls:
            trace: dict[str, Any] = {"mode": "browser", "start_url": url}
            resp = page.goto(url, wait_until="domcontentloaded")
            trace["landed_url"] = page.url
            trace["landed_status"] = resp.status if resp else None
            trace["cookies_before"] = sorted(c["name"] for c in ctx.cookies())
            if page.locator("#butAgree").count():
                time.sleep(1.1)
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.click("#butAgree")
                trace["after_click_url"] = page.url
            else:
                trace["result"] = "no banner shown"
            trace["cookies_after"] = [
                {"name": c["name"], "domain": c["domain"], "httpOnly": c["httpOnly"],
                 "secure": c["secure"], "sameSite": c["sameSite"], "expires": c["expires"]}
                for c in ctx.cookies()
            ]
            traces.append(trace)
            time.sleep(1.1)
        for c in ctx.cookies():
            pc.client.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."), path=c["path"])
        browser.close()
    return traces


def ensure_access(pc: PoliteClient, mode: str) -> list[dict[str, Any]]:
    starts = [f"{WWW}/RFQ/RfqDates.aspx?category=recent", f"{FILES}/dodwarning.aspx?goto=%2f"]
    if mode == "browser":
        try:
            return accept_banner_browser(pc, starts)
        except Exception as exc:  # noqa: BLE001 - fall back to plain form POST
            log.warning("Playwright banner acceptance failed (%s); falling back to form POST", exc)
    return [accept_banner_form(pc, f"{WWW}/RFQ/RfqDates.aspx?category=recent"),
            accept_banner_form(pc, f"{RFQ_ARCHIVE}/")]


# --------------------------------------------------------------------------
# Fixture writing
# --------------------------------------------------------------------------
class FixtureWriter:
    def __init__(self, root: Path):
        self.root = root
        self.manifest: list[dict[str, Any]] = []

    def save(self, rel: str, content: bytes | str, url: str | None = None, note: str = "") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        path.write_bytes(data)
        self.manifest.append(
            {"path": rel.replace("\\", "/"), "url": url, "bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "note": note,
             "fetched_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
        )
        return path

    def write_manifest(self) -> None:
        mpath = self.root / "manifest.json"
        old: list[dict[str, Any]] = []
        if mpath.exists():
            old = json.loads(mpath.read_text(encoding="utf-8"))
        merged = {m["path"]: m for m in old}
        merged.update({m["path"]: m for m in self.manifest})
        merged = {k: v for k, v in merged.items() if (self.root / k).exists()}
        mpath.write_text(json.dumps(sorted(merged.values(), key=lambda m: m["path"]), indent=1),
                         encoding="utf-8")


def fetch_html(pc: PoliteClient, fw: FixtureWriter, url: str, rel: str, note: str = "") -> str | None:
    r = pc.get(url)
    if r.status_code != 200 or is_banner(r):
        log.error("Failed %s -> %s banner=%s", url, r.status_code, is_banner(r))
        return None
    fw.save(rel, r.content, url, note)
    return r.text


def pdf_to_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            parts.append(page.extract_text() or "")
            parts.append(f"<<<PAGE {i} END>>>")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# Parsing helpers (discovery-grade only)
# --------------------------------------------------------------------------
def parse_index_line(line: str) -> dict[str, str]:
    out, pos = {}, 0
    for name, width in INDEX_LAYOUT:
        out[name] = line[pos:pos + width]
        pos += width
    return out


def recent_dates_from_html(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for a in soup.find_all("a", href=re.compile(r"/Archive/in\d{6}\.txt$", re.I)):
        tr = a.find_parent("tr")
        links = {x.get_text(strip=True): x["href"] for x in tr.find_all("a", href=True)} if tr else {}
        rows.append({"index_file": a.get_text(strip=True), "index_url": a["href"], "links": links})
    return rows


def award_rows(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="ctl00_cph1_grdAwardSearch")
    rows = []
    if not table:
        return rows
    for tr in table.find_all("tr", recursive=False):
        spans = {s["id"].rsplit("_", 1)[-1]: s for s in tr.find_all("span", id=True)}
        if "lblAwardBasicNumber" not in spans:
            continue
        pdfs = [a["href"] for a in tr.find_all("a", href=re.compile(r"\.PDF$", re.I))]
        pkg = [a["href"] for a in tr.find_all("a", href=re.compile(r"AwdRec\.aspx", re.I))]
        rows.append({
            "award_basic": (spans["lblAwardBasicNumber"].get_text(" ", strip=True).split() or [""])[0],
            "delivery_order": spans["lblDeliveryOrder"].get_text(" ", strip=True).split(" ")[0]
            if "lblDeliveryOrder" in spans else "",
            "cage": spans["lblCage"].get_text(strip=True) if "lblCage" in spans else "",
            "total": spans["lblTotalContactPrice"].get_text(strip=True) if "lblTotalContactPrice" in spans else "",
            "award_date": spans.get("lblAwardDate").get_text(strip=True) if "lblAwardDate" in spans else "",
            "nsn": spans["lblNsn"].get_text(strip=True) if "lblNsn" in spans else "",
            "solicitation": spans["lblSolicitation"].get_text(strip=True) if "lblSolicitation" in spans else "",
            "pdf_urls": list(dict.fromkeys(pdfs)),
            "package_urls": list(dict.fromkeys(pkg)),
        })
    return rows


def aspnet_hidden_fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    return {
        i["name"]: i.get("value", "")
        for i in form.find_all("input")
        if i.get("type") == "hidden" and i.get("name")
    }


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
class Discovery:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.fw = FixtureWriter(Path(args.fixtures))
        self.raw = Path(args.raw)
        self.raw.mkdir(parents=True, exist_ok=True)
        self.pc = PoliteClient(min_interval=args.min_interval)
        self.fscs = args.fscs.split(",")
        self.dates: list[str] = [d for d in (args.dates or "").split(",") if d]
        self.index_rows: list[dict[str, str]] = []
        self.award_samples: list[dict[str, Any]] = []
        self.state_path = self.raw / "discovery_state.json"
        self.state: dict[str, Any] = (
            json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        )

    def save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    # ---- access -----------------------------------------------------------
    def step_access(self) -> None:
        traces = ensure_access(self.pc, self.args.banner)
        # Also record what an un-cookied client sees (status codes) for the docs.
        probe = PoliteClient(min_interval=self.args.min_interval)
        observed = []
        for url in (f"{WWW}/RFQ/RfqDates.aspx?category=recent", f"{RFQ_ARCHIVE}/in{self._some_date()}.txt"):
            r = probe.request("GET", url, follow=False)
            observed.append({"url": url, "status": r.status_code, "location": r.headers.get("location"),
                             "content_type": r.headers.get("content-type"), "is_banner": is_banner(r)})
            if "dibbs2" in url and is_banner(r):
                self.fw.save("dibbs/rfq_dates/dodwarning_dibbs2.html", r.content, url,
                             "DoD banner served in place (HTTP 200) by dibbs2 file host")
        r = probe.request("GET", f"{WWW}/dodwarning.aspx?goto=/rfq/rfqdates.aspx?category=recent", follow=False)
        self.fw.save("dibbs/rfq_dates/dodwarning_www.html", r.content, str(r.url),
                     "DoD Notice and Consent banner form (www host)")
        probe.client.close()
        # Non-browser fallback test on a fresh client (records whether form POST alone works)
        fb = PoliteClient(min_interval=self.args.min_interval)
        fallback = [accept_banner_form(fb, f"{WWW}/RFQ/RfqDates.aspx?category=recent"),
                    accept_banner_form(fb, f"{RFQ_ARCHIVE}/in{self._some_date()}.txt")]
        r = fb.get(f"{WWW}/RFQ/RfqDates.aspx?category=recent")
        fallback.append({"verify_www_after_form": r.status_code, "banner": is_banner(r)})
        r = fb.get(f"{RFQ_ARCHIVE}/in{self._some_date()}.txt")
        fallback.append({"verify_dibbs2_after_form": r.status_code, "banner": is_banner(r),
                         "content_type": r.headers.get("content-type")})
        fb.client.close()
        result = {"primary": traces, "uncookied_observations": observed, "form_fallback_test": fallback}
        self.fw.save("dibbs/rfq_dates/banner_access_trace.json", json.dumps(result, indent=1),
                     note="How the DoD banner was accepted; cookie names; form-POST fallback test")

    def _some_date(self) -> str:
        if self.dates:
            return self.dates[0]
        d = dt.date.today() - dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        return d.strftime("%y%m%d")

    # ---- A: RFQ dates pages ----------------------------------------------
    def step_rfq_dates(self) -> None:
        html = fetch_html(self.pc, self.fw, f"{WWW}/RFQ/RfqDates.aspx?category=recent",
                          "dibbs/rfq_dates/rfqdates_recent.html", "Recent RFQs and Downloads (daily file links)")
        for cat in ("issue", "close"):
            fetch_html(self.pc, self.fw, f"{WWW}/Rfq/RfqDates.aspx?category={cat}",
                       f"dibbs/rfq_dates/rfqdates_{cat}.html", f"RFQ dates page category={cat}")
        fetch_html(self.pc, self.fw, f"{WWW}/Rfq/RfqFileDefs.aspx", "dibbs/rfq_dates/rfq_file_defs.html",
                   "Official help page with in/ca/bq/as file layouts")
        fetch_html(self.pc, self.fw, f"{WWW}/Refs/help/Quoting/Batch/BatchFormat.aspx",
                   "dibbs/rfq_dates/batch_quote_format.html", "Batch quote (bq) 121-column layout")
        fetch_html(self.pc, self.fw, f"{WWW}/Rfq/RfqFsc.aspx", "dibbs/rfq_dates/rfq_fsc.html",
                   "RFQs by FSC page")
        if html:
            rows = recent_dates_from_html(html)
            if not self.dates:
                self.dates = [re.search(r"in(\d{6})", r["index_file"]).group(1) for r in rows[: self.args.num_dates]]
            self.state["recent_dates"] = rows
            self.save_state()
        # First page of the per-post-date RFQ listing (paginated GridView, 50/page)
        if self.dates:
            d = self.dates[0]
            mmddyyyy = f"{d[2:4]}-{d[4:6]}-20{d[0:2]}"
            fetch_html(self.pc, self.fw,
                       f"{WWW}/RFQ/RfqRecs.aspx?category=post&TypeSrch=dt&Value={mmddyyyy}",
                       f"dibbs/rfq_dates/rfqrecs_post_{d}_page1.html",
                       "RFQ listing for one post date, GridView page 1")

    # ---- B: index + batch quote files ------------------------------------
    def step_rfq_index(self) -> None:
        if not self.dates:
            self.dates = [re.search(r"in(\d{6})", r["index_file"]).group(1)
                          for r in self.state.get("recent_dates", [])][: self.args.num_dates]
        all_rows: list[dict[str, str]] = []
        for i, d in enumerate(self.dates):
            url = f"{RFQ_ARCHIVE}/in{d}.txt"
            r = self.pc.get(url)
            if r.status_code != 200 or is_banner(r):
                log.error("index %s -> %s", url, r.status_code)
                continue
            (self.raw / f"in{d}.txt").write_bytes(r.content)
            text = r.content.decode("latin-1")
            lines = text.splitlines()
            if len(r.content) > self.args.max_index_bytes and i >= 2:
                keep = "\r\n".join(lines[: self.args.truncate_lines]) + "\r\n"
                self.fw.save(f"dibbs/rfq_index/in{d}.truncated.txt", keep.encode("latin-1"), url,
                             f"TRUNCATED to first {self.args.truncate_lines} of {len(lines)} lines")
            else:
                self.fw.save(f"dibbs/rfq_index/in{d}.txt", r.content, url, f"full index, {len(lines)} lines")
            for ln in lines:
                if ln.strip():
                    row = parse_index_line(ln)
                    row["_post_date"] = d
                    row["_len"] = str(len(ln))
                    all_rows.append(row)
            if i < self.args.bq_dates:
                zurl = f"{RFQ_ARCHIVE}/bq{d}.zip"
                zr = self.pc.get(zurl)
                if zr.status_code == 200 and zr.content[:2] == b"PK":
                    self.fw.save(f"dibbs/rfq_index/bq{d}.zip", zr.content, zurl,
                                 "batch quote zip: bqYYMMDD.txt (121-col CSV) + asYYMMDD.txt (approved sources)")
                    with zipfile.ZipFile(io.BytesIO(zr.content)) as z:
                        self.state.setdefault("bq_members", {})[d] = [
                            {"name": n.filename, "size": n.file_size} for n in z.infolist()]
        self.index_rows = all_rows
        self.fw.save("dibbs/rfq_index/index_stats.json", json.dumps(self.index_stats(all_rows), indent=1),
                     note="Counts by solicitation type (9th char) and FSC across downloaded index files")
        self.save_state()
        self.probe_big_files()

    def probe_big_files(self) -> None:
        """Record size of the 'ca' (all PDFs) zip without downloading the body."""
        if not self.dates:
            return
        url = f"{RFQ_ARCHIVE}/ca{self.dates[0]}.zip"
        host = urlparse(url).netloc
        self.pc._wait(host)
        with self.pc.client.stream("GET", url) as r:
            self.pc._last[host] = time.monotonic()
            self.state["ca_zip_probe"] = {"url": url, "status": r.status_code,
                                          "content_length": r.headers.get("content-length"),
                                          "content_type": r.headers.get("content-type")}
        log.info("ca zip probe: %s", self.state["ca_zip_probe"])
        self.save_state()

    def index_stats(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        by_type_lines = collections.Counter(r["solicitation_number"][8] for r in rows)
        sols = {}
        for r in rows:
            sols.setdefault(r["solicitation_number"], r)
        by_type_sols = collections.Counter(s[8] for s in sols)
        fsc_lines = collections.Counter(r["nsn_or_part_number"][:4] for r in rows)
        target = {}
        for f in self.fscs:
            rr = [r for r in rows if r["nsn_or_part_number"].startswith(f)]
            target[f] = {
                "lines": len(rr),
                "solicitations": len({r["solicitation_number"] for r in rr}),
                "lines_by_type": dict(collections.Counter(r["solicitation_number"][8] for r in rr)),
                "solicitations_by_type": dict(collections.Counter(
                    s[8] for s in {r["solicitation_number"] for r in rr})),
                "set_aside": dict(collections.Counter(r["set_aside"] for r in rr)),
            }
        per_file = collections.Counter(r["_post_date"] for r in rows)
        return {
            "files": dict(per_file),
            "total_lines": len(rows),
            "unique_solicitations": len(sols),
            "record_lengths": dict(collections.Counter(r["_len"] for r in rows)),
            "lines_by_type_9th_char": dict(by_type_lines),
            "solicitations_by_type_9th_char": dict(by_type_sols),
            "item_type": dict(collections.Counter(r["item_type"] for r in rows)),
            "set_aside": dict(collections.Counter(r["set_aside"] for r in rows)),
            "set_aside_pct": dict(collections.Counter(r["set_aside_pct"] for r in rows)),
            "amsc": dict(collections.Counter(r["amsc"] for r in rows)),
            "unit_of_issue_top": dict(collections.Counter(r["unit_of_issue"] for r in rows).most_common(15)),
            "buyer_code_top": dict(collections.Counter(r["buyer_code"] for r in rows).most_common(15)),
            "lines_per_solicitation": dict(sorted(collections.Counter(
                collections.Counter(r["solicitation_number"] for r in rows).values()).items())),
            "top_fsc_lines": dict(fsc_lines.most_common(30)),
            "target_fscs": target,
        }

    # ---- C/D/E: records, PDFs, NSN pages ---------------------------------
    def step_rfq_records(self) -> None:
        sols = self.args.solicitations.split(",") if self.args.solicitations else DEFAULT_SOLICITATIONS
        nsns_done: set[str] = set()
        rec_summary = []
        for sn in sols[: self.args.max_records]:
            url = f"{WWW}/rfq/rfqrec.aspx?sn={sn}"
            html = fetch_html(self.pc, self.fw, url, f"dibbs/rfq_record/rfqrec_{sn}.html", "RFQ Package View")
            if not html:
                continue
            soup = BeautifulSoup(html, "lxml")
            pdf = soup.find("a", href=re.compile(r"/Downloads/RFQ/.+\.PDF$", re.I))
            nsn_links = [a["href"] for a in soup.find_all("a", href=re.compile(r"RFQNsn\.aspx", re.I))]
            rec_summary.append({"sn": sn, "pdf": pdf["href"] if pdf else None, "nsn_links": sorted(set(nsn_links))})
            if pdf:
                pr = self.pc.get(pdf["href"])
                if pr.status_code == 200 and pr.content[:4] == b"%PDF":
                    self.fw.save(f"dibbs/rfq_pdf/{sn}.pdf", pr.content, pdf["href"], "RFQ solicitation PDF")
                    self.fw.save(f"dibbs/rfq_pdf/{sn}.txt", pdf_to_text(pr.content), pdf["href"],
                                 "pdfplumber extract_text(), pages separated by <<<PAGE n END>>>")
                else:
                    log.error("PDF %s -> %s %s", pdf["href"], pr.status_code, pr.headers.get("content-type"))
            for link in sorted(set(nsn_links)):
                m = re.search(r"value=([^&]+)", link)
                if not m or m.group(1) in nsns_done or len(nsns_done) >= self.args.max_nsn_pages:
                    continue
                nsns_done.add(m.group(1))
                fetch_html(self.pc, self.fw, link.replace("&amp;", "&"),
                           f"dibbs/nsn/rfqnsn_{m.group(1)}.html", "Public RFQ NSN view")
        self.state["records"] = rec_summary
        self.save_state()

    # ---- F: awards --------------------------------------------------------
    def step_awards(self) -> None:
        home = fetch_html(self.pc, self.fw, f"{WWW}/Awards/", "dibbs/awards/awards_search_page.html",
                          "Award search form (cross-page postback to AwdRecs.aspx)")
        for cat in ("post", "awddt"):
            fetch_html(self.pc, self.fw, f"{WWW}/Awards/AwdDates.aspx?category={cat}",
                       f"dibbs/awards/awddates_{cat}.html", f"Award dates list, category={cat}")
        # Award listings for a couple of post dates (page 1), plus page 2 via postback
        post_dates = self.args.award_dates.split(",") if self.args.award_dates else []
        if not post_dates:
            d = dt.date.today() - dt.timedelta(days=1)
            post_dates = [(d - dt.timedelta(days=k)).strftime("%m-%d-%Y") for k in (0, 1)]
        for i, d in enumerate(post_dates):
            url = f"{WWW}/Awards/AwdRecs.aspx?Category=post&TypeSrch=cq&Value={d}"
            html = fetch_html(self.pc, self.fw, url, f"dibbs/awards/awdrecs_post_{d}_page1.html",
                              "Awards posted on date, GridView page 1 (50 rows)")
            if html and i == 0:
                data = aspnet_hidden_fields(html)
                data.update({"__EVENTTARGET": "ctl00$cph1$grdAwardSearch", "__EVENTARGUMENT": "Page$2"})
                r = self.pc.post(url, data=data)
                if r.status_code == 200 and not is_banner(r):
                    self.fw.save(f"dibbs/awards/awdrecs_post_{d}_page2.html", r.content, url,
                                 "Page 2 obtained via __doPostBack replay (POST __EVENTTARGET/ARGUMENT=Page$2)")
        # GET-style searches (no form needed): by NSN list and by CAGE
        nsns = self._target_nsns()
        chunks = [nsns[i:i + 40] for i in range(0, min(len(nsns), 80), 40)]
        rows: list[dict[str, Any]] = []
        for k, chunk in enumerate(chunks, 1):
            url = f"{WWW}/Awards/AwdRecs.aspx?Category=nsn&TypeSrch=cq&Value={','.join(chunk)}&Scope=all"
            html = fetch_html(self.pc, self.fw, url, f"dibbs/awards/awdrecs_nsn_search_{k}.html",
                              f"Award search Category=nsn, {len(chunk)} comma-separated NSNs, Scope=all")
            if html:
                rows += award_rows(html)
        # Also prove the form POST path (cross-page postback) works for a single NSN
        if home:
            data = aspnet_hidden_fields(home)
            data.update({"ctl00$cph1$ddlCategory": "nsn", "ctl00$cph1$txtValue": nsns[0] if nsns else "",
                         "ctl00$cph1$rblScope": "all", "ctl00$cph1$ddlSort": "nsn",
                         "ctl00$cph1$butSubmit": "Submit", "ctl00$cph1$txtEndDate": "",
                         "ctl00$cph1$txtStartDate": "", "ctl00$cph1$txtLowCnt": "", "ctl00$cph1$txtHiCnt": "",
                         "ctl00$ddlNavigation": "", "ctl00$ddlGotoDB": "", "ctl00$txtValue": ""})
            r = self.pc.post(f"{WWW}/Awards/AwdRecs.aspx", data=data)
            if r.status_code == 200 and not is_banner(r):
                self.fw.save("dibbs/awards/awdrecs_form_post_nsn.html", r.content, f"{WWW}/Awards/AwdRecs.aspx",
                             "Same search via the form's cross-page POST")
        self.state["award_rows_sample"] = rows[:200]
        self.save_state()
        # Pick award PDFs: recent awards in target FSCs, one per award type
        # (9th char of the PIID: V = automated PO, P = manual PO, F = delivery
        # order, D = IDC/AIDC basic), plus any explicitly requested contracts.
        picked: list[dict[str, Any]] = []
        min_year = dt.date.today().year - 1
        for want in ("V", "P", "F", "D"):
            for row in rows:
                piid = row["delivery_order"] or row["award_basic"]
                year = int(row["award_date"][-4:]) if row["award_date"][-4:].isdigit() else 0
                # DO rows link both the basic's PDF and the order's PDF
                # (<BASIC><ORDER>.PDF); keep only the PDF of the PIID itself.
                own = [u for u in row["pdf_urls"] if Path(urlparse(u).path).stem.upper().endswith(piid.upper())]
                if (own and row["nsn"][:4] in self.fscs and len(piid) > 8
                        and piid[8] == want and year >= min_year):
                    picked.append({**row, "pdf_urls": own})
                    break
        for spec in [c for c in self.args.award_contracts.split(",") if c]:
            contract, _, dlv = spec.partition(":")
            url = f"{WWW}/Awards/AwdRec.aspx?contract={contract}&dlv={dlv}&cnt="
            r = self.pc.get(url)
            if r.status_code == 200 and not is_banner(r):
                soup = BeautifulSoup(r.text, "lxml")
                pdfs = [a["href"] for a in soup.find_all("a", href=re.compile(r"/Downloads/Awards/.+\.PDF$", re.I))]
                picked.append({"award_basic": contract, "delivery_order": dlv, "cage": "", "nsn": "",
                               "pdf_urls": list(dict.fromkeys(pdfs)), "package_urls": [url],
                               "package_html": r.content})
        for row in picked:
            if not row["pdf_urls"]:
                continue
            pdf_url = row["pdf_urls"][0]
            name = Path(urlparse(pdf_url).path).stem
            r = self.pc.get(pdf_url)
            if r.status_code == 200 and r.content[:4] == b"%PDF" and len(r.content) <= self.args.max_pdf_bytes:
                self.fw.save(f"dibbs/award_pdf/{name}.pdf", r.content, pdf_url, "Award/order PDF")
                self.fw.save(f"dibbs/award_pdf/{name}.txt", pdf_to_text(r.content), pdf_url, "pdfplumber text")
            else:
                log.warning("skipped award PDF %s (%s, %d bytes)", pdf_url, r.status_code, len(r.content))
            html = row.pop("package_html", None)
            if html is not None:
                self.fw.save(f"dibbs/awards/awdrec_{name}.html", html, row["package_urls"][0], "Award Package View")
            else:
                for pkg in row["package_urls"][:1]:
                    fetch_html(self.pc, self.fw, pkg, f"dibbs/awards/awdrec_{name}.html", "Award Package View")
        self.award_samples = picked
        self.state["award_samples"] = picked
        self.save_state()

    def _target_nsns(self) -> list[str]:
        rows = self.index_rows
        if not rows:
            for p in sorted(self.raw.glob("in*.txt")):
                rows += [parse_index_line(ln) for ln in p.read_text(encoding="latin-1").splitlines() if ln.strip()]
        out: list[str] = []
        for r in rows:
            n = r["nsn_or_part_number"].strip()
            if n[:4] in self.fscs and r["item_type"] == "1" and n not in out:
                out.append(n)
        # interleave FSCs so each chunk covers all of them
        by = collections.defaultdict(list)
        for n in out:
            by[n[:4]].append(n)
        mixed = []
        while any(by.values()):
            for f in self.fscs:
                if by[f]:
                    mixed.append(by[f].pop(0))
        return mixed

    # ---- G: CAGE ----------------------------------------------------------
    def step_cage(self) -> None:
        cages = self.args.cages.split(",") if self.args.cages else []
        if not cages:
            samples = self.award_samples or self.state.get("award_samples", [])
            cages = list(dict.fromkeys(r["cage"] for r in samples if r.get("cage")))[:3]
        for cage in cages:
            fetch_html(self.pc, self.fw, f"{WWW}/Refs/cage.aspx?Cage={cage}", f"cage/dibbs_cage_{cage}.html",
                       "DIBBS public CAGE pop-up (SAM CAGE info)")
        # cage.dla.mil: record the gate; NEVER submit the usage agreement.
        probe = []
        for cage in cages[:2]:
            for url in (f"https://cage.dla.mil/Search/Results?q={cage}&page=1",
                        f"https://cage.dla.mil/Search/Details?id={cage}"):
                r = self.pc.request("GET", url, follow=False)
                probe.append({"url": url, "status": r.status_code, "location": r.headers.get("location")})
        r = self.pc.request("GET", "https://cage.dla.mil/Home/UsageAgree", follow=False)
        self.fw.save("cage/cage_dla_mil_usage_agree.html", r.content, "https://cage.dla.mil/Home/UsageAgree",
                     "Usage agreement gate (NOT accepted)")
        probe.append({"url": "https://cage.dla.mil/Home/UsageAgree", "status": r.status_code})
        self.fw.save("cage/cage_dla_mil_probe.json", json.dumps(probe, indent=1),
                     note="cage.dla.mil redirects every search to /Home/UsageAgree; not accepted")

    # ---- H: USAspending ---------------------------------------------------
    def step_usaspending(self) -> None:
        api = "https://api.usaspending.gov/api/v2"
        fields = ["Award ID", "Recipient Name", "Recipient UEI", "recipient_id", "Award Amount",
                  "Total Outlays", "Description", "Start Date", "End Date", "Awarding Agency",
                  "Awarding Sub Agency", "Funding Sub Agency", "Contract Award Type", "NAICS", "PSC",
                  "Place of Performance State Code", "Last Modified Date", "generated_internal_id"]
        today = dt.date.today()
        start = (today - dt.timedelta(days=365)).isoformat()
        bodies = {
            "spending_by_award_dla_psc.json": {
                "filters": {
                    "agencies": [{"type": "awarding", "tier": "subtier", "name": "Defense Logistics Agency"}],
                    "psc_codes": self.fscs,
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": start, "end_date": today.isoformat()}],
                },
                "fields": fields, "limit": 25, "page": 1, "sort": "Start Date", "order": "desc",
                "subawards": False,
            },
        }
        piids = self.args.piids.split(",") if self.args.piids else []
        if piids:
            bodies["spending_by_award_piid_keyword.json"] = {
                "filters": {"keywords": piids, "award_type_codes": ["A", "B", "C", "D"],
                            "time_period": [{"start_date": start, "end_date": today.isoformat()}]},
                "fields": fields, "limit": 25, "page": 1, "sort": "Award Amount", "order": "desc",
                "subawards": False,
            }
        detail_id = None
        for name, body in bodies.items():
            r = self.pc.post(f"{api}/search/spending_by_award/", json=body)
            payload = {"request": {"method": "POST", "url": f"{api}/search/spending_by_award/", "body": body},
                       "status": r.status_code, "response": r.json() if r.headers.get(
                           "content-type", "").startswith("application/json") else r.text}
            self.fw.save(f"usaspending/{name}", json.dumps(payload, indent=1), f"{api}/search/spending_by_award/")
            if r.status_code == 200 and (not detail_id or "piid" in name):
                res = r.json().get("results") or []
                if res:  # prefer a DIBBS-style PIID from the keyword search
                    detail_id = res[-1].get("generated_internal_id")
        if detail_id:
            url = f"{api}/awards/{detail_id}/"
            r = self.pc.get(url)
            if r.status_code == 200:
                self.fw.save("usaspending/award_detail.json", json.dumps(r.json(), indent=1), url,
                             "GET /api/v2/awards/<generated_internal_id>/")


STEPS = ["access", "rfq_dates", "rfq_index", "rfq_records", "awards", "cage", "usaspending"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", default=",".join(STEPS), help=f"comma list of steps ({','.join(STEPS)})")
    ap.add_argument("--banner", choices=["browser", "form"], default="browser",
                    help="accept DoD banner via headless Chromium (default) or plain form POST")
    ap.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    ap.add_argument("--raw", default=str(DEFAULT_RAW), help="scratch dir (gitignored)")
    ap.add_argument("--dates", default="", help="RFQ post dates as YYMMDD,YYMMDD (default: most recent)")
    ap.add_argument("--num-dates", type=int, default=5)
    ap.add_argument("--bq-dates", type=int, default=2, help="how many dates to keep bq zips for")
    ap.add_argument("--max-index-bytes", type=int, default=2_000_000,
                    help="index files larger than this are truncated (except the first 2 dates)")
    ap.add_argument("--truncate-lines", type=int, default=300)
    ap.add_argument("--fscs", default=",".join(DEFAULT_FSCS))
    ap.add_argument("--solicitations", default="", help="comma list of solicitation numbers (no hyphens)")
    ap.add_argument("--max-records", type=int, default=10)
    ap.add_argument("--max-nsn-pages", type=int, default=10)
    ap.add_argument("--award-dates", default="", help="award post dates MM-DD-YYYY,... (default: last 2 days)")
    ap.add_argument("--award-contracts", default="",
                    help="extra award package views to fetch, CONTRACT[:DELIVERYORDER],...")
    ap.add_argument("--max-pdf-bytes", type=int, default=2_000_000, help="skip award PDFs bigger than this")
    ap.add_argument("--cages", default="", help="CAGE codes for the CAGE step (default: from award samples)")
    ap.add_argument("--piids", default="", help="PIIDs for USAspending keyword search")
    ap.add_argument("--min-interval", type=float, default=1.1, help="seconds between requests per host")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    disc = Discovery(args)
    needs_dibbs = any(s in steps for s in ("rfq_dates", "rfq_index", "rfq_records", "awards", "cage"))
    if needs_dibbs and "access" not in steps:
        ensure_access(disc.pc, args.banner)
    try:
        for step in STEPS:
            if step in steps:
                log.info("=== step %s ===", step)
                getattr(disc, f"step_{step}")()
    finally:
        disc.fw.write_manifest()
        (disc.raw / "request_log.json").write_text(json.dumps(disc.pc.request_log, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
