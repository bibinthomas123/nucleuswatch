"""Shared HTTP session with retries and a small on-disk response cache.

Broker APIs are run by small teams and some are rate limited (Lasair allows
100 calls/hour with a personal token), so repeated lookups of the same object
are served from cache instead of hitting the broker again.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

DEFAULT_TIMEOUT = 60.0
DEFAULT_TTL = 3600  # seconds


class BrokerError(Exception):
    """Raised when a broker returns an error or an unusable response."""


def cache_dir() -> Path:
    root = os.environ.get("NUCLEUSWATCH_CACHE")
    if root:
        return Path(root)
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nucleuswatch"


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = f"nucleuswatch/{__version__}"
    return s


_SESSION = _session()


def request_json(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    data: dict | None = None,
    headers: dict | None = None,
    use_cache: bool = True,
    ttl: float = DEFAULT_TTL,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Make a request and return decoded JSON, using the disk cache if allowed.

    Auth headers are deliberately left out of the cache key so tokens are
    never written to disk.
    """
    path = _cache_path(method, url, params, json_body, data, ".json")

    if use_cache and _fresh(path, ttl):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass  # corrupt cache entry; fall through and refetch

    r = _send(method, url, params=params, json_body=json_body, data=data,
              headers=headers, timeout=timeout)
    try:
        result = r.json()
    except ValueError as exc:
        raise BrokerError(f"{url}: response was not JSON") from exc

    if use_cache:
        _store(path, json.dumps(result).encode())
    return result


def _cache_path(method, url, params, json_body, data, suffix) -> Path:
    key_src = json.dumps([method, url, params, json_body, data], sort_keys=True, default=str)
    return cache_dir() / f"{hashlib.sha256(key_src.encode()).hexdigest()}{suffix}"


def _fresh(path: Path, ttl: float) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < ttl


def _store(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError:
        pass  # caching is best effort


def _send(method, url, *, params=None, json_body=None, data=None, headers=None,
          timeout=DEFAULT_TIMEOUT) -> requests.Response:
    try:
        r = _SESSION.request(
            method, url, params=params, json=json_body, data=data,
            headers=headers, timeout=timeout,
        )
    except requests.RequestException as exc:
        raise BrokerError(f"request to {url} failed: {exc}") from exc

    if r.status_code == 401:
        raise BrokerError(f"{url}: unauthorized (check your API token)")
    if r.status_code == 429:
        raise BrokerError(f"{url}: rate limit exceeded, try again later")
    if r.status_code == 404:
        raise BrokerError(f"{url}: not found")
    if not r.ok:
        raise BrokerError(f"{url}: HTTP {r.status_code}: {r.text[:200]}")
    return r
