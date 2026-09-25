"""Shared HTTP session: per-host rate limiting + retry with backoff.

Every public endpoint this project touches is unauthenticated, so there are no
keys here. Rate limits are conservative relative to Polymarket's published
limits so a full-season backfill can run unattended.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

# requests/second per host (Polymarket publishes limits per 10s window; stay well under)
HOST_RPS = {
    "gamma-api.polymarket.com": 8.0,
    "clob.polymarket.com": 10.0,
    "data-api.polymarket.com": 15.0,
    "statsapi.mlb.com": 12.0,   # 2-second linescore polling of a full live slate
}
DEFAULT_RPS = 5.0


class _Bucket:
    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)


_buckets: dict[str, _Bucket] = {}
_buckets_lock = threading.Lock()
_local = threading.local()


def _bucket(host: str) -> _Bucket:
    with _buckets_lock:
        if host not in _buckets:
            _buckets[host] = _Bucket(HOST_RPS.get(host, DEFAULT_RPS))
        return _buckets[host]


def _session() -> requests.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16))
        s.headers["User-Agent"] = "pmsports-research/0.1"
        _local.session = s
    return s


class HTTPError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status


def get_json(url: str, params: dict | None = None, *, retries: int = 6, timeout: float = 30.0):
    host = urlparse(url).netloc
    delay = 1.0
    for attempt in range(retries):
        _bucket(host).wait()
        try:
            r = _session().get(url, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            retry_after = r.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
            delay = min(delay * 2, 60)
            continue
        raise HTTPError(r.status_code, r.url, r.text)
    raise RuntimeError("unreachable")
