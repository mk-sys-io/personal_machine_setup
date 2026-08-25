"""Provider registry: identity, endpoints, per-provider probe bounds."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import PROBE_TIMEOUT

ProviderId = Literal["openrouter", "nararouter", "opencode", "nim", "gemini"]


@dataclass(frozen=True)
class Provider:
    label: str
    auth_key: str
    env_var: str
    base_url: str
    curated_file: str
    endpoint: str
    # Per-model probe bound; override where the provider is known to be slow.
    # Zen needs 45 s: measured first-token latencies reach 25.5 s
    # (nemotron-3-ultra-free) — NIM's 15 s would silently drop working models.
    probe_timeout: int = PROBE_TIMEOUT


# id|label|auth.json key|env var|base URL|curated file|validate endpoint
PROVIDERS: dict[ProviderId, Provider] = {
    "openrouter": Provider(
        "OpenRouter (free)",
        "openrouter",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1",
        "openrouter-free.json",
        "https://openrouter.ai/api/v1/models",
    ),
    "nararouter": Provider(
        "NaraRouter (free)",
        "nararouter",
        "NARAROUTER_API_KEY",
        "https://router.bynara.id/v1",
        "nararouter-free.json",
        "https://router.bynara.id/api/plans",
    ),
    "opencode": Provider(
        "OpenCode Zen",
        "opencode",
        "OPENCODE_API_KEY",
        "https://opencode.ai/zen/v1",
        "opencode.json",
        "https://opencode.ai/zen/v1/models",
        probe_timeout=45,
    ),
    "nim": Provider(
        "NVIDIA NIM (live)",
        "nim",
        "NVIDIA_NIM_API_KEY",
        "https://integrate.api.nvidia.com/v1",
        "nim.json",
        "https://integrate.api.nvidia.com/v1/models",
    ),
    "gemini": Provider(
        "Google AI Studio (Gemini)",
        "gemini",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini.json",
        "https://generativelanguage.googleapis.com/v1beta/models",
        # Gemini needs 35 s: native tool-bearing requests measured
        # 20.3-32.5 s TTFB (gemma-4-31b-it); bare requests are 3-5x faster
        # but tools are the realistic Pi request shape.
        probe_timeout=35,
    ),
}
# Fetch-based providers come first by design: auth prompts their keys and
# maybe_discover offers them before the slow multi-minute live probes.
PROVIDER_ORDER: tuple[ProviderId, ...] = (
    "openrouter", "nararouter", "opencode", "nim", "gemini",
)
# Discovery strategy per provider — two verbs, two trust levels:
# probe sends a real chat request per model (slow, verifies the model
# actually works); fetch downloads a free-model list (one GET, trusts
# provider metadata — nothing is verified live).
PROBE_PROVIDERS: tuple[ProviderId, ...] = ("opencode", "nim", "gemini")
FETCH_PROVIDERS: tuple[ProviderId, ...] = ("openrouter", "nararouter")
