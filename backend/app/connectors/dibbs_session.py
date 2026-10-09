"""DIBBS DoD Notice and Consent banner handling.

Both DIBBS hosts gate content behind the banner (docs/data_sources.md section 1):
  * www.dibbs.bsm.dla.mil answers 302 -> /dodwarning.aspx?goto=...
  * dibbs2.bsm.dla.mil serves the banner HTML *in place* (200) at the file URL.
Accepting it (button "OK") sets the `dw` cookie, which is all later requests need.

Mechanisms:
  * "browser" (default, as specified): Playwright headless Chromium clicks #butAgree; the cookies are
    copied into the httpx client used for bulk requests.
  * "form": a plain HTTP POST of the banner form's hidden fields + butAgree=OK (verified to work).
The session falls back from browser to form if Playwright is unavailable or fails.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.connectors.base import PoliteHttp
from app.connectors.parsers.dibbs_html import is_banner

log = logging.getLogger(__name__)

WWW = "https://www.dibbs.bsm.dla.mil"
DIBBS2 = "https://dibbs2.bsm.dla.mil"


class BannerError(RuntimeError):
    pass


def _banner_form(html: str) -> tuple[str, dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", id="FrmBody") or soup.find("form")
    if form is None:
        raise BannerError("banner form not found")
    fields = {i.get("name"): i.get("value", "") for i in form.find_all("input", attrs={"type": "hidden"}) if i.get("name")}
    fields["butAgree"] = "OK"
    return form.get("action") or "", fields


class DibbsSession:
    def __init__(self, http: PoliteHttp, mode: str = "browser") -> None:
        self.http = http
        self.mode = mode
        self.accepted_hosts: set[str] = set()

    # ------------------------------------------------------------------ acceptance
    def _accept_form(self, banner_url: str, banner_html: str) -> httpx.Response:
        action, fields = _banner_form(banner_html)
        resp = self.http.post(urljoin(banner_url, action), data=fields)
        if resp.status_code not in (200, 302):
            raise BannerError(f"banner POST returned {resp.status_code}")
        self.accepted_hosts.add(urlparse(banner_url).netloc)
        return resp

    def _accept_browser(self, host_root: str) -> None:
        from playwright.sync_api import sync_playwright

        target = f"{host_root}/dodwarning.aspx?goto={quote('/')}" if "dibbs2" in host_root else f"{WWW}/RFQ/RfqDates.aspx?category=recent"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(user_agent=self.http.client.headers.get("User-Agent"))
                page = ctx.new_page()
                page.goto(target, wait_until="domcontentloaded", timeout=60000)
                if page.locator("#butAgree").count():
                    with page.expect_navigation(timeout=60000):
                        page.click("#butAgree")
                for c in ctx.cookies():
                    self.http.client.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."), path=c.get("path", "/"))
            finally:
                browser.close()
        self.accepted_hosts.add(urlparse(host_root).netloc)

    def ensure_accepted(self, url: str) -> None:
        """Proactively accept the banner for the URL's host (browser mode)."""
        host = urlparse(url).netloc
        if host in self.accepted_hosts or self.mode != "browser":
            return
        try:
            self._accept_browser(f"https://{host}")
        except Exception as exc:  # Playwright missing / browser not installed / navigation failed
            log.warning("Playwright banner acceptance failed (%s); falling back to form POST", exc)
            self.mode = "form"

    # ------------------------------------------------------------------ fetching
    def fetch(self, url: str, method: str = "GET", data: dict | None = None) -> bytes:
        """GET/POST a DIBBS URL, transparently accepting the banner. Returns the body bytes."""
        self.ensure_accepted(url)
        for _ in range(3):
            resp = self.http.request(method, url, data=data) if data is not None else self.http.request(method, url)
            loc = resp.headers.get("location", "")
            if resp.status_code in (301, 302, 303) and "dodwarning" in loc.lower():
                banner_url = urljoin(url, loc)
                banner = self.http.get(banner_url)
                self._accept_form(banner_url, banner.text)
                continue
            if resp.status_code in (301, 302, 303):
                url = urljoin(url, loc)
                method, data = "GET", None
                continue
            if resp.status_code == 404:
                raise FileNotFoundError(url)
            resp.raise_for_status()
            if is_banner(resp.content):
                # dibbs2: banner served in place; the POST response *is* the requested file.
                posted = self._accept_form(url, resp.text)
                if posted.status_code == 200 and not is_banner(posted.content):
                    return posted.content
                continue
            return resp.content
        raise BannerError(f"could not get past the DoD banner for {url}")
