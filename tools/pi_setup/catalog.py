"""Per-provider model-catalog fetchers and the non-chat pre-filter."""
from __future__ import annotations

import json
from collections.abc import Callable
from functools import partial

from .config import FETCH_RETRIES, is_non_chat
from .errors import CatalogError
from .fsio import require_key
from .http import _fetch_json, _http_get
from .providers import PROVIDERS, ProviderId

CatalogFetcher = Callable[[], list[str]]


def fetch_catalog(prov: ProviderId) -> list[str]:
    url = f"{PROVIDERS[prov].base_url}/models"
    raw = _fetch_json(url, require_key(prov))
    models = raw.get("data")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        model_id = m.get("id")
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return sorted(set(ids))


def filter_catalog(ids: list[str]) -> tuple[list[str], list[str]]:
    """Split ids into (to_probe, skipped_by_keyword)."""
    to_probe: list[str] = []
    skipped: list[str] = []
    for mid in ids:
        if is_non_chat(mid):
            skipped.append(mid)
        else:
            to_probe.append(mid)
    return to_probe, skipped


def fetch_free_models_openrouter() -> list[str]:
    """Fetch OpenRouter catalog; return free models (pricing=$0).

    Applies NON_CHAT_KEYWORDS pre-filter (checklist step 1) to exclude
    non-chat models (embedding, audio, safety, etc.) before pricing check.
    """
    raw = _fetch_json("https://openrouter.ai/api/v1/models", "")
    models = raw.get("data")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        if is_non_chat(mid):
            continue
        pricing = m.get("pricing")
        if not isinstance(pricing, dict):
            continue
        prompt_price = str(pricing.get("prompt", "1"))
        completion_price = str(pricing.get("completion", "1"))
        if prompt_price == "0" and completion_price == "0":
            ids.append(mid)
    return sorted(set(ids))


def fetch_free_models_nararouter() -> list[str]:
    """Fetch NaraRouter /api/plans; return free-tier model IDs."""
    raw = _fetch_json("https://router.bynara.id/api/plans", "")
    data = raw.get("data")
    if not isinstance(data, list) or not data:
        return []
    free_plan = data[0]
    if not isinstance(free_plan, dict):
        return []
    models = free_plan.get("models")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if isinstance(m, str) and m:
            ids.append(m)
    return sorted(set(ids))


def fetch_catalog_gemini() -> list[str]:
    """Fetch Google AI Studio native catalog; return chat-eligible model IDs.

    Uses the native models endpoint with x-goog-api-key header auth.
    Filters by supportedGenerationMethods containing 'generateContent' and
    excludes non-chat models via NON_CHAT_KEYWORDS.
    """
    key = require_key("gemini")
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    status, body, reason = _http_get(
        url, "", use_auth=False, extra_headers={"x-goog-api-key": key}
    )
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
    models = raw.get("models")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name", "")
        if not isinstance(name, str) or not name:
            continue
        # name field is "models/gemini-2.5-flash" — strip prefix
        model_id = name.removeprefix("models/")
        if not model_id:
            continue
        # Layer 1: supportedGenerationMethods must include generateContent
        methods = m.get("supportedGenerationMethods", [])
        if not isinstance(methods, list) or "generateContent" not in methods:
            continue
        # Layer 2: keyword exclusion for non-chat models
        if is_non_chat(model_id):
            continue
        ids.append(model_id)
    return sorted(set(ids))


# Discovery strategy registries — adding a provider means one registry entry
# (plus its fetcher), never another branch in shared command code.
CATALOG_FETCHERS: dict[ProviderId, CatalogFetcher] = {
    "opencode": partial(fetch_catalog, "opencode"),
    "nim": partial(fetch_catalog, "nim"),
    "gemini": fetch_catalog_gemini,
}
FREE_LIST_FETCHERS: dict[ProviderId, CatalogFetcher] = {
    "openrouter": fetch_free_models_openrouter,
    "nararouter": fetch_free_models_nararouter,
}
