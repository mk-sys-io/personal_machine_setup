"""HTTP layer: retrying GETs with backoff; JSON fetch with typed errors."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .config import FETCH_RETRIES, HTTP_TIMEOUT, RETRY_BACKOFF
from .errors import CatalogError


def _http_get(
    url: str,
    key: str,
    *,
    use_auth: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, bytes | None, str]:
    """GET with optional auth header; returns (status, body, error reason).

    HTTP errors are final (no retry) and reported as their status code.
    Transient OSErrors (timeout/DNS/TLS) retry up to FETCH_RETRIES with
    backoff; after exhausting, status is 0 with the last error reason.
    When use_auth=False, no Authorization header is sent; pass credentials
    via extra_headers instead (Google AI Studio uses x-goog-api-key — keeps
    the key out of URLs and therefore out of error messages).
    """
    reason = ""
    headers: dict[str, str] = {"User-Agent": "pi-setup/1.0"}
    if use_auth and key:
        headers["Authorization"] = f"Bearer {key}"
    if extra_headers:
        headers.update(extra_headers)
    for attempt in range(FETCH_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.status, resp.read(), ""
        except urllib.error.HTTPError as e:
            return e.code, None, ""
        except OSError as e:
            reason = str(e)
            if attempt < FETCH_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])
    return 0, None, reason


def http_status(url: str, key: str) -> int:
    """HTTP status for url, or 0 if the request failed after retries."""
    status, _, _ = _http_get(url, key)
    return status


def _fetch_json(url: str, key: str, *, use_auth: bool = True) -> dict:
    """GET url, parse JSON response. Raises CatalogError on failure."""
    status, body, reason = _http_get(url, key, use_auth=use_auth)
    if status == 0:
        raise CatalogError(
            f"GET {url} failed after {FETCH_RETRIES} attempts: {reason}"
        )
    if status != 200:
        raise CatalogError(f"GET {url} returned HTTP {status}")
    assert body is not None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CatalogError(f"GET {url} returned an unexpected payload") from e
    if not isinstance(raw, dict):
        raise CatalogError(f"GET {url} returned an unexpected payload")
    return raw
