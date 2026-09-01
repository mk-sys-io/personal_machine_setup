"""Emit the Pi extension provider manifest (JSON).

The extension's provider list is generated, not hand-maintained — the single
source of truth is pi_setup/providers.py (shared registry + Pi probe/pi map).
pi-setup auth writes the rendered manifest to PI_PROVIDERS_JSON at the end of
the copy flow.
"""
from __future__ import annotations

import json

from .providers import PI_MAP, PROBE_MAP, PROVIDER_ORDER, PROVIDERS


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
        if pid in PROBE_MAP:
            # Basename only — the extension joins it with its own CURATED_DIR.
            entry["curatedFile"] = PROBE_MAP[pid].curated_file
        if p.chat is not None:
            entry["api"] = (
                "google-generative-ai"
                if p.chat.protocol == "gemini"
                else "openai-completions"
            )
        if pid in PI_MAP:
            entry["fallbackToStored"] = PI_MAP[pid].fallback_to_stored
        providers.append(entry)
    return {"providers": providers}


def render_manifest() -> str:
    return json.dumps(build_manifest(), indent=2) + "\n"
