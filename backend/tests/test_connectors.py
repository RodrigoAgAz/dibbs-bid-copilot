"""Connector plumbing: banner acceptance (both hosts), rate limiting, retries, raw cache. No network."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from app.connectors.base import HostRateLimiter, PoliteHttp
from app.connectors.dibbs import FixtureDibbsSource, LiveDibbsSource
from app.connectors.dibbs_session import DibbsSession

D = Path(__file__).parent / "fixtures" / "dibbs"
WWW_BANNER = (D / "rfq_dates" / "dodwarning_www.html").read_bytes()
D2_BANNER = (D / "rfq_dates" / "dodwarning_dibbs2.html").read_bytes()
INDEX = (D / "rfq_index" / "in261008.txt").read_bytes()
RECORD = (D / "rfq_record" / "rfqrec_SPE7L127U0037.html").read_bytes()


class FakeDibbs:
    """Simulates both DIBBS hosts' DoD banner behaviour."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.posts = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url))
        assert "DIBBSBidCopilot" in request.headers["user-agent"]
        has_dw = "dw=" in request.headers.get("cookie", "")
        host = request.url.host
        if request.method == "POST":
            self.posts += 1
            body = request.content.decode()
            assert "butAgree=OK" in body and "__VIEWSTATE" in body
            cookie = {"set-cookie": "dw=ok; Path=/; HttpOnly"}
            if host.startswith("dibbs2"):
                return httpx.Response(200, content=INDEX, headers=cookie)  # the POST response IS the file
            return httpx.Response(302, headers={**cookie, "location": "https://www.dibbs.bsm.dla.mil//rfq/rfqrec.aspx?sn=SPE7L127U0037"})
        if host.startswith("dibbs2"):
            return httpx.Response(200, content=INDEX if has_dw else D2_BANNER, headers={"content-type": "text/plain"})
        if "dodwarning" in url:
            return httpx.Response(200, content=WWW_BANNER, headers={"content-type": "text/html"})
        if not has_dw:
            return httpx.Response(302, headers={"location": "/dodwarning.aspx?goto=/rfq/rfqrec.aspx?sn=SPE7L127U0037"})
        return httpx.Response(200, content=RECORD, headers={"content-type": "text/html"})


def _http(handler, sleeps=None) -> PoliteHttp:
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "DIBBSBidCopilot/test"})
    return PoliteHttp("dibbs-test", client=client, min_interval=0.0, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_banner_accepted_by_form_post_on_both_hosts():
    fake = FakeDibbs()
    session = DibbsSession(_http(fake), mode="form")
    body = session.fetch("https://www.dibbs.bsm.dla.mil/rfq/rfqrec.aspx?sn=SPE7L127U0037")
    assert b"SPE7L1-27-U-0037" in body
    data = session.fetch("https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/in261008.txt")
    assert data == INDEX
    assert fake.posts == 2  # one acceptance per host
    # Cookie reused afterwards: no more banner POSTs.
    session.fetch("https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/in261008.txt")
    assert fake.posts == 2


def test_browser_mode_falls_back_to_form_when_playwright_fails(monkeypatch):
    fake = FakeDibbs()
    session = DibbsSession(_http(fake), mode="browser")
    monkeypatch.setattr(session, "_accept_browser", lambda host: (_ for _ in ()).throw(RuntimeError("no browser")))
    assert b"SPE7L1-27-U-0037" in session.fetch("https://www.dibbs.bsm.dla.mil/rfq/rfqrec.aspx?sn=SPE7L127U0037")
    assert session.mode == "form"


def test_retries_with_backoff_on_429_and_5xx():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(429 if attempts["n"] == 1 else 503 if attempts["n"] == 2 else 200, content=b"ok")

    sleeps: list[float] = []
    http = _http(handler, sleeps)
    assert http.get("https://example.test/x").content == b"ok"
    assert attempts["n"] == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_gives_up_after_max_retries():
    http = _http(lambda r: httpx.Response(500))
    http.max_retries = 2
    with pytest.raises(httpx.HTTPStatusError):
        http.get("https://example.test/x")


def test_rate_limiter_spaces_requests_per_host():
    lim = HostRateLimiter(min_interval=0.2)
    t0 = time.monotonic()
    lim.wait("a.test-ratelimit")
    lim.wait("a.test-ratelimit")
    lim.wait("b.test-ratelimit")  # other host: no wait
    assert 0.18 <= time.monotonic() - t0 < 0.6


def test_raw_cache_and_live_source_with_fake_site():
    fake = FakeDibbs()
    src = LiveDibbsSource(http=_http(fake), banner_mode="form")
    page = src.record_page("SPE7L127U0037")
    assert b"CASTER" in page
    cached = list(Path(src.http.cache_root, "dibbs-test").rglob("rfqrec_SPE7L127U0037.html"))
    assert cached and cached[0].read_bytes() == page


def test_fixture_source_serves_samples():
    src = FixtureDibbsSource()
    dates = src.rfq_index_dates()
    assert len(dates) == 5 and dates[-1].isoformat() == "2026-10-08"
    assert len(src.index_lines(dates[-1])) == 1769
    assert src.rfq_pdf("SPE4A627T0486")[:4] == b"%PDF"
    assert src.record_page("NOPE") is None
    assert len(src.awards_by_post_date(dates[-1])) == 2


def test_missing_file_redirect_is_not_found():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://dibbs2.bsm.dla.mil/FileNotFound.aspx", "set-cookie": "dw=1"})

    session = DibbsSession(_http(handler), mode="form")
    with pytest.raises(FileNotFoundError):
        session.fetch("https://dibbs2.bsm.dla.mil/Downloads/Awards/07OCT26/NOPE.PDF")
