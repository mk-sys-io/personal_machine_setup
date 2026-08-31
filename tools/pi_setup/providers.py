"""Provider registry shim: re-exports from the shared provider_registry module.

Strategy tuples are derived from the vault (single source of truth), falling
back to the built-in provider.probe.strategy when the vault has no entry.
"""
from __future__ import annotations

from provider_registry.credentials import JsonCredentialStore
from provider_registry.providers import (
    PROVIDER_ORDER,
    PROVIDERS,
    ChatConfig,
    PiConfig,
    ProbeConfig,
    Provider,
    ProviderId,
    get_provider,
    list_providers,
)


def _resolved_strategy(pid: ProviderId) -> str:
    """Vault strategy first, then provider default."""
    store = JsonCredentialStore()
    s = store.strategy(pid)
    if s is not None:
        return s
    p = PROVIDERS[pid]
    return p.probe.strategy if p.probe is not None else "fetch"


PROBE_PROVIDERS: tuple[ProviderId, ...] = tuple(
    p for p in PROVIDER_ORDER if _resolved_strategy(p) == "probe"
)
FETCH_PROVIDERS: tuple[ProviderId, ...] = tuple(
    p for p in PROVIDER_ORDER if _resolved_strategy(p) == "fetch"
)

__all__ = [
    "FETCH_PROVIDERS",
    "PROBE_PROVIDERS",
    "PROVIDER_ORDER",
    "PROVIDERS",
    "ChatConfig",
    "PiConfig",
    "ProbeConfig",
    "Provider",
    "ProviderId",
    "get_provider",
    "list_providers",
]
