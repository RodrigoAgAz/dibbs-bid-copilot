"""Small fixed-window rate limiter backed by Redis when available, memory otherwise."""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

from app.config import get_settings


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mem: dict[str, list[float]] = defaultdict(list)
        self._redis = None
        url = get_settings().redis_url
        if url:
            try:
                import redis

                client = redis.Redis.from_url(url, socket_connect_timeout=0.5)
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None

    def hit(self, key: str, limit: int, window_s: int) -> bool:
        """Record a hit; return True if allowed, False if the limit is exceeded."""
        if self._redis is not None:
            bucket = f"rl:{key}:{int(time.time() // window_s)}"
            try:
                n = self._redis.incr(bucket)
                if n == 1:
                    self._redis.expire(bucket, window_s)
                return int(n) <= limit
            except Exception:
                pass
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._mem[key] if now - t < window_s]
            hits.append(now)
            self._mem[key] = hits
            return len(hits) <= limit

    def reset(self) -> None:
        with self._lock:
            self._mem.clear()


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce(request: Request, scope: str, ident: str, limit: int, window_s: int) -> None:
    limiter = get_limiter()
    ip = client_ip(request)
    ok_ip = limiter.hit(f"{scope}:ip:{ip}", limit * 3, window_s)
    ok_id = limiter.hit(f"{scope}:id:{ident.lower()}", limit, window_s)
    if not (ok_ip and ok_id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please wait and try again.")
