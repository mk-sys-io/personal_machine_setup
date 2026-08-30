"""Emit the Pi extension provider manifest (JSON).

The extension's provider list is generated, not hand-maintained — the single
source of truth is provider_registry/providers.py. pi-setup auth writes the
rendered manifest to PI_PROVIDERS_JSON at the end of the copy flow.
"""
from __future__ import annotations

import json

from .providers import PROVIDER_ORDER, PROVIDERS


def build_manifest() -> dict[str, list[dict[str, object]]]:
    providers: list[dict[str, object]] = []
    for pid in PROVIDER_ORDER:
        p = PROVIDERS[pid]
        entry: dict[str, object] = {
            "id": p.id,
            "name": p.label,
            "baseUrl": p.base_url,
            "authEnv": p.env_var,
        }
        if p.probe is not None:
            # Basename only — the extension joins it with its own CURATED_DIR.
            entry["curatedFile"] = p.probe.curated_file
        if p.chat is not None:
            entry["api"] = (
                "google-generative-ai"
                if p.chat.protocol == "gemini"
                else "openai-completions"
            )
        if p.pi is not None:
            entry["fallbackToStored"] = p.pi.fallback_to_stored
        providers.append(entry)
    return {"providers": providers}


def render_manifest() -> str:
    return json.dumps(build_manifest(), indent=2) + "\n"
