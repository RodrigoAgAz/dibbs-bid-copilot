"""Shared connector plumbing: polite HTTP (UA, 1 req/s per host across processes, backoff) and raw-response caching."""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


_shared = {"conn": None, "retry_at": 0.0}


def _shared_redis():
    """Process-wide Redis connection for the cross-process rate limit (None without REDIS_URL / when down)."""
    url = get_settings().redis_url
    if not url or time.monotonic() < _shared["retry_at"]:
        return None
    if _shared["conn"] is None:
        try:
            from redis import Redis

            conn = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=2)
            conn.ping()
            _shared["conn"] = conn
        except Exception as exc:  # pragma: no cover - depends on environment
            log.warning("rate limiter: Redis unavailable (%s); limiting per process", exc)
            _shared["retry_at"] = time.monotonic() + 60
            return None
    return _shared["conn"]


class HostRateLimiter:
    """At most one request per `min_interval` seconds per host.

    With Redis (REDIS_URL) the limit is shared by every process - both worker pools, the API and the
    scheduler - via a SET NX PX key per host; otherwise it is process-wide.
    """

    _lock = threading.Lock()
    _last: dict[str, float] = {}

    def __init__(self, min_interval: float = 1.0) -> None:
        self.min_interval = min_interval

    def wait(self, host: str) -> None:
        if self._wait_shared(host):
            return
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delay = self.min_interval - (now - last)
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()

    def _wait_shared(self, host: str) -> bool:
        conn = _shared_redis()
        if conn is None:
            return False
        key, ms = f"ratelimit:{host}", max(int(self.min_interval * 1000), 1)
        try:
            while not conn.set(key, "1", nx=True, px=ms):
                time.sleep(max(conn.pttl(key), 20) / 1000)
            return True
        except Exception as exc:  # pragma: no cover - depends on environment
            log.warning("rate limiter: Redis error (%s); limiting per process", exc)
            _shared["conn"] = None
            _shared["retry_at"] = time.monotonic() + 60
            return False


class PoliteHttp:
    def __init__(
        self,
        source: str,
        client: httpx.Client | None = None,
        min_interval: float = 1.0,
        max_retries: int = 4,
        cache: bool = True,
        sleep=time.sleep,
    ) -> None:
        s = get_settings()
        self.source = source
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"User-Agent": s.scraper_user_agent},
            follow_redirects=False,
        )
        self.client.headers.setdefault("User-Agent", s.scraper_user_agent)
        self.limiter = HostRateLimiter(min_interval)
        self.max_retries = max_retries
        self.cache = cache
        self.cache_root = Path(s.raw_cache_dir)
        self._sleep = sleep

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        host = urlparse(url).netloc
        attempt = 0
        while True:
            self.limiter.wait(host)
            try:
                resp = self.client.request(method, url, **kw)
                if resp.status_code not in RETRY_STATUSES:
                    return resp
                err: Exception | None = httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                err = exc
            attempt += 1
            if attempt > self.max_retries:
                raise err  # type: ignore[misc]
            backoff = 2**attempt + random.uniform(0, 1)
            log.warning("%s %s failed (%s); retry %d in %.1fs", method, url, err, attempt, backoff)
            self._sleep(backoff)

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)

    def save_raw(self, name: str, content: bytes, day: date | None = None) -> Path | None:
        if not self.cache:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:150] or hashlib.sha1(content).hexdigest()
        d = self.cache_root / self.source / (day or date.today()).isoformat()
        d.mkdir(parents=True, exist_ok=True)
        p = d / safe
        p.write_bytes(content)
        return p
