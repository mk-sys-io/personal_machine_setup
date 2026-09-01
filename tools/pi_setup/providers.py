"""Pi-specific provider registry: shared registry + probe/pi config map.

Re-exports the shared provider registry (identity, endpoints, chat contract)
from provider_registry and adds the Pi-only probe/fetch config (strategy,
curated file, endpoint, probe timeout) and Pi-extension flags keyed by
provider id. Strategy tuples derive from the gopass store (single source of
truth), falling back to the built-in probe strategy when the store has no
entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from provider_registry.config import GOPASS_ENTRY_PREFIX, gopass_show_field
from provider_registry.providers import (
    PROVIDER_ORDER,
    PROVIDERS,
    Provider,
    ProviderId,
    get_provider,
    list_providers,
)

from .config import PROBE_TIMEOUT

Strategy = Literal["probe", "fetch"]


@dataclass(frozen=True)
class ProbeConfig:
    """Model-discovery config for pi_setup (probe live / fetch free list)."""

    strategy: Strategy
    curated_file: str
    endpoint: str
    probe_timeout: int = PROBE_TIMEOUT


@dataclass(frozen=True)
class PiConfig:
    """Pi-extension-only flags carried into the generated manifest."""

    fallback_to_stored: bool = True


# Per-provider probe config (strategy, curated file, endpoint, probe timeout).
PROBE_MAP: dict[ProviderId, ProbeConfig] = {
    "openrouter": ProbeConfig(
        strategy="fetch",
        curated_file="openrouter-free.json",
        endpoint="https://openrouter.ai/api/v1/models",
    ),
    "nararouter": ProbeConfig(
        strategy="fetch",
        curated_file="nararouter-free.json",
        endpoint="https://router.bynara.id/api/plans",
    ),
    "opencode": ProbeConfig(
        strategy="probe",
        curated_file="opencode.json",
        endpoint="https://opencode.ai/zen/v1/models",
        # Zen needs 45 s: measured first-token latencies reach 25.5 s
        # (nemotron-3-ultra-free) — NIM's 15 s would silently drop working models.
        probe_timeout=45,
    ),
    "nim": ProbeConfig(
        strategy="probe",
        curated_file="nim.json",
        endpoint="https://integrate.api.nvidia.com/v1/models",
    ),
    "gemini": ProbeConfig(
        strategy="probe",
        curated_file="gemini.json",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models",
        # Gemini needs 35 s: native tool-bearing requests measured
        # 20.3-32.5 s TTFB (gemma-4-31b-it); bare requests are 3-5x faster
        # but tools are the realistic Pi request shape.
        probe_timeout=35,
    ),
}

# Per-provider Pi-extension flags.
PI_MAP: dict[ProviderId, PiConfig] = {
    "gemini": PiConfig(fallback_to_stored=False),
}


def _resolved_strategy(pid: ProviderId) -> str:
    """gopass strategy first, then provider default."""
    s = gopass_show_field(f"{GOPASS_ENTRY_PREFIX}/{pid}", "strategy")
    if s is not None:
        return s
    p = PROBE_MAP.get(pid)
    return p.strategy if p is not None else "fetch"


PROBE_PROVIDERS: tuple[ProviderId, ...] = tuple(
    p for p in PROVIDER_ORDER if _resolved_strategy(p) == "probe"
)
FETCH_PROVIDERS: tuple[ProviderId, ...] = tuple(
    p for p in PROVIDER_ORDER if _resolved_strategy(p) == "fetch"
)

__all__ = [
    "FETCH_PROVIDERS",
    "PI_MAP",
    "PROBE_MAP",
    "PROBE_PROVIDERS",
    "PROVIDER_ORDER",
    "PROVIDERS",
    "PiConfig",
    "ProbeConfig",
    "Provider",
    "ProviderId",
    "Strategy",
    "get_provider",
    "list_providers",
]
