"""Live chat-probing engines, status classification, and probe/fetch commands."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

from .catalog import CATALOG_FETCHERS, FREE_LIST_FETCHERS, filter_catalog
from .config import GEMINI_TOOLS, PI_TOOLS, PROBE_PACE, PROBE_PROMPT
from .errors import CatalogError, ToolError, UsageError
from .fsio import curated_file, require_key, write_curated
from .http import http_status
from .providers import FETCH_PROVIDERS, PROVIDERS, ProviderId

ProbeFn = Callable[[str, str, int], tuple[int, float]]
ClassifyFn = Callable[[int], str]


def _drain_stream(resp: Any, *, start: float, timeout: int) -> tuple[bytes, float]:
    """Read an SSE stream to completion (or the total-time bound)."""
    chunks: list[bytes] = []
    while True:
        if time.time() - start > timeout:
            break  # total-time exceeded — too slow to be usable
        chunk = resp.read(4096)
        if not chunk:
            break  # stream complete
        chunks.append(chunk)
    return b"".join(chunks), time.time() - start


def probe_model(prov: ProviderId, model_id: str, key: str, timeout: int) -> tuple[int, float]:
    """Probe an OpenAI-format provider (opencode/nim) with a Pi-like request.

    Returns (HTTP status, total seconds).

    Status 200 = working, 0 = network/protocol error (unusable body),
    -1 = total-time exceeded (too slow or completely unresponsive).
    """
    url = f"{PROVIDERS[prov].base_url}/chat/completions"
    payload = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            "tools": PI_TOOLS,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": 1024,
        }
    ).encode()
    headers: dict[str, str] = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "pi-setup/1.0",
    }
    if prov == "nim":
        headers["X-BILLING-INVOKE-ORIGIN"] = "Pi"

    req = urllib.request.Request(url, data=payload, headers=headers)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body, latency = _drain_stream(resp, start=start, timeout=timeout)
            if latency > timeout:
                return -1, latency
            if not body or b"data: " not in body:
                return 0, latency
            return resp.status, latency
    except urllib.error.HTTPError as e:
        return e.code, time.time() - start
    except OSError:
        return -1, time.time() - start


def probe_model_gemini(model_id: str, key: str, timeout: int) -> tuple[int, float]:
    """Probe a Gemini model via the native :generateContent protocol (SSE).

    Pi's extension talks to Google AI Studio natively — its OpenAI-compat shim
    rejects OpenAI-only fields with an opaque gzip-masked 400 — so the probe
    knocks on the same door production uses. Same contract as probe_model:
    200 = working, 0 = unusable body, -1 = total-time exceeded.
    """
    url = f"{PROVIDERS['gemini'].base_url}/models/{model_id}:generateContent?alt=sse"
    payload = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": PROBE_PROMPT}]}],
            "tools": GEMINI_TOOLS,
            "generationConfig": {"maxOutputTokens": 1024},
        }
    ).encode()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "pi-setup/1.0",
        "x-goog-api-key": key,
    }
    req = urllib.request.Request(url, data=payload, headers=headers)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body, latency = _drain_stream(resp, start=start, timeout=timeout)
            if latency > timeout:
                return -1, latency
            if not body or b"data: " not in body:
                return 0, latency
            return resp.status, latency
    except urllib.error.HTTPError as e:
        return e.code, time.time() - start
    except OSError:
        return -1, time.time() - start


def classify_zen(status: int) -> str:
    """Classify a Zen chat-probe HTTP status into a keep/drop category.

    The Zen account is deliberately credit-free: billing is checked BEFORE
    upstream dispatch, so every paid model answers 401/402 CreditsError —
    probing can never mistake paid for free.

      working    200                      -> keep (free + responsive)
      throttled  429 FreeUsageLimitError  -> keep (free-tier rate limit;
                                                still proves free membership)
      paid       401/402 CreditsError     -> drop (paid => unwanted)
      dead       anything else / timeout  -> drop (unusable right now)
    """
    if status == 200:
        return "working"
    if status == 429:
        return "throttled"
    if status in (401, 402):
        return "paid"
    return "dead"


def _classify_working(status: int) -> str:
    """Plain classifier: only a completed 200 proves the model works."""
    return "working" if status == 200 else "dead"


ZEN_KEEP_CATEGORIES = frozenset({"working", "throttled"})
DEFAULT_KEEP_CATEGORIES = frozenset({"working"})


# Probe strategy registries — per-provider quirks (native gemini protocol,
# Zen's billing-based freeness classification) live here instead of branching
# inside shared loop/command code.
PROBES: dict[ProviderId, ProbeFn] = {
    "opencode": partial(probe_model, "opencode"),
    "nim": partial(probe_model, "nim"),
    "gemini": probe_model_gemini,
}
CLASSIFIERS: dict[ProviderId, ClassifyFn] = {
    "opencode": classify_zen,
    "nim": _classify_working,
    "gemini": _classify_working,
}
KEEP_CATEGORIES: dict[ProviderId, frozenset[str]] = {
    "opencode": ZEN_KEEP_CATEGORIES,
    "nim": DEFAULT_KEEP_CATEGORIES,
    "gemini": DEFAULT_KEEP_CATEGORIES,
}


def run_probe_loop(
    prov: ProviderId,
    ids: Sequence[str],
    key: str,
    timeout: int,
) -> list[tuple[str, int, float]]:
    """Probe each id with pacing; return (model_id, status, latency) tuples.

    The per-provider request engine comes from PROBES; callers classify
    statuses via CLASSIFIERS/KEEP_CATEGORIES. A -1 (total-time exceeded) is
    retried once immediately — free-tier cold starts spike; the retry result
    stands.
    """
    fire = PROBES[prov]
    results: list[tuple[str, int, float]] = []
    last = len(ids) - 1
    for i, model_id in enumerate(ids):
        status, latency = fire(model_id, key, timeout)
        if status == -1:
            status, latency = fire(model_id, key, timeout)
        results.append((model_id, status, latency))
        if i != last:
            time.sleep(PROBE_PACE)
    return results


def _preflight_connectivity(prov: ProviderId) -> None:
    """Bail before a long probe run when the network is down.

    Skipped for '!cmd'/'$ENV' key references (Pi resolves them later) and
    providers without a catalog endpoint.
    """
    p = PROVIDERS[prov]
    if not p.endpoint:
        return
    try:
        key_val = require_key(prov)
    except ToolError:
        return
    if key_val and not key_val.startswith("!") and "$" not in key_val:
        status = http_status(p.endpoint, key_val)
        if status == 0:
            raise ToolError(
                f"no network connectivity to {p.endpoint} — check your internet connection"
            )


def cmd_fetch_free(prov: ProviderId, *, write: bool) -> None:
    """Fetch free-tier models for openrouter/nararouter and write curated file.

    List-based discovery: one GET per provider, no live verification —
    freeness comes from provider metadata, not a test request.
    """
    if prov not in FETCH_PROVIDERS:
        raise ToolError(f"'{prov}' does not support free-model discovery")
    p = PROVIDERS[prov]
    print(f"Fetching {p.label} free-model list (no live verification)...")
    ids = FREE_LIST_FETCHERS[prov]()
    if not ids:
        print(f"  {p.label}: no free models found")
        return
    print(f"  {p.label}: {len(ids)} free models")
    if write:
        path = curated_file(prov)
        write_curated(path, {"patterns": sorted(ids)}, fail_verb="write")
        print(f"Wrote {len(ids)} models to {path}")


def cmd_probe(prov: ProviderId, *, write: bool) -> None:
    """Probe one provider's live catalog; keep models its classifier passes.

    One generic path for every probe-based provider: connectivity preflight
    (with --write), catalog fetch, keyword pre-filter, paced probing with one
    retry on timeout, per-provider classification, report, and (with --write)
    a fresh curated file — no merge with previous results.
    """
    if prov in FETCH_PROVIDERS:
        raise UsageError(
            f"'{prov}' is list-based (no live probing) — use: pi-setup fetch {prov}"
        )
    p = PROVIDERS[prov]
    if write:
        _preflight_connectivity(prov)
    key = require_key(prov)
    raw_ids = CATALOG_FETCHERS[prov]()
    if not raw_ids:
        raise CatalogError(f"live catalog for '{prov}' is empty")
    ids, skipped = filter_catalog(raw_ids)
    skip_note = f", skipped {len(skipped)} non-chat" if skipped else ""
    print(
        f"Probing {p.label} ({len(ids)} models, timeout: {p.probe_timeout}s{skip_note})..."
    )

    classify = CLASSIFIERS[prov]
    keep = KEEP_CATEGORIES[prov]
    results = run_probe_loop(prov, ids, key, p.probe_timeout)

    kept: list[tuple[str, float]] = []
    counts: dict[str, int] = {}
    for model_id, status, latency in results:
        category = classify(status)
        counts[category] = counts.get(category, 0) + 1
        if category in keep:
            kept.append((model_id, latency))

    breakdown = ", ".join(f"{n} {cat}" for cat, n in sorted(counts.items()))
    print(f"  {p.label}: kept {len(kept)} of {len(ids)} probed ({breakdown})")
    if kept:
        fast = sorted(kept, key=lambda x: x[1])[:5]
        print(f"    Fastest: {', '.join(f'{m} ({lat:.1f}s)' for m, lat in fast)}")
    if write:
        path = curated_file(prov)
        write_curated(path, {"patterns": sorted(m for m, _ in kept)}, fail_verb="write")
        print(f"Wrote {len(kept)} models to {path}")
