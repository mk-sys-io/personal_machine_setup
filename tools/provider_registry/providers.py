"""Provider registry: identity, endpoints, per-consumer config.

Composition: a core Provider (fields used by all consumers) plus optional
typed sub-objects for consumer-specific concerns — ChatConfig (ask.py +
SearXNG plugin), ProbeConfig (pi_setup), PiConfig (Pi extension).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .config import PROBE_TIMEOUT
from .errors import ToolError

ProviderId = Literal["openrouter", "nararouter", "opencode", "nim", "gemini"]
Strategy = Literal["probe", "fetch"]
Protocol = Literal["openai", "gemini"]


@dataclass(frozen=True)
class ChatConfig:
    """Request/response contract for chat consumers (ask.py, SearXNG plugin)."""

    protocol: Protocol
    model_env: str
    default_model: str


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


@dataclass(frozen=True)
class Provider:
    id: ProviderId
    label: str
    auth_key: str
    env_var: str
    base_url: str
    chat: ChatConfig | None = None  # ask.py + plugin (2 consumers)
    probe: ProbeConfig | None = None  # pi_setup only (1 consumer)
    pi: PiConfig | None = None  # Pi extension only (1 consumer)


# id|label|auth.json key|env var|base URL|chat|probe|pi
# Strategy: probe = live chat-probe (opencode/nim/gemini), fetch = free-model
# list download (openrouter/nararouter). Protocol: gemini = native
# :generateContent, others = OpenAI-compatible /chat/completions.
# default_model values are the first (fastest) curated model per provider.
PROVIDERS: dict[ProviderId, Provider] = {
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter (free)",
        auth_key="openrouter",
        env_var="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="OPENROUTER_MODEL",
            default_model="cohere/north-mini-code:free",
        ),
        probe=ProbeConfig(
            strategy="fetch",
            curated_file="openrouter-free.json",
            endpoint="https://openrouter.ai/api/v1/models",
        ),
    ),
    "nararouter": Provider(
        id="nararouter",
        label="NaraRouter (free)",
        auth_key="nararouter",
        env_var="NARAROUTER_API_KEY",
        base_url="https://router.bynara.id/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="NARAROUTER_MODEL",
            default_model="agnes-2.0-flash",
        ),
        probe=ProbeConfig(
            strategy="fetch",
            curated_file="nararouter-free.json",
            endpoint="https://router.bynara.id/api/plans",
        ),
    ),
    "opencode": Provider(
        id="opencode",
        label="OpenCode Zen",
        auth_key="opencode",
        env_var="OPENCODE_API_KEY",
        base_url="https://opencode.ai/zen/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="OPENCODE_MODEL",
            default_model="big-pickle",
        ),
        probe=ProbeConfig(
            strategy="probe",
            curated_file="opencode.json",
            endpoint="https://opencode.ai/zen/v1/models",
            # Zen needs 45 s: measured first-token latencies reach 25.5 s
            # (nemotron-3-ultra-free) — NIM's 15 s would silently drop working models.
            probe_timeout=45,
        ),
    ),
    "nim": Provider(
        id="nim",
        label="NVIDIA NIM (live)",
        auth_key="nim",
        env_var="NVIDIA_NIM_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="NIM_MODEL",
            default_model="meta/llama-3.1-70b-instruct",
        ),
        probe=ProbeConfig(
            strategy="probe",
            curated_file="nim.json",
            endpoint="https://integrate.api.nvidia.com/v1/models",
        ),
    ),
    "gemini": Provider(
        id="gemini",
        label="Google AI Studio (Gemini)",
        auth_key="gemini",
        env_var="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        chat=ChatConfig(
            protocol="gemini",
            model_env="GEMINI_MODEL",
            default_model="gemini-3-flash-preview",
        ),
        probe=ProbeConfig(
            strategy="probe",
            curated_file="gemini.json",
            endpoint="https://generativelanguage.googleapis.com/v1beta/models",
            # Gemini needs 35 s: native tool-bearing requests measured
            # 20.3-32.5 s TTFB (gemma-4-31b-it); bare requests are 3-5x faster
            # but tools are the realistic Pi request shape.
            probe_timeout=35,
        ),
        pi=PiConfig(fallback_to_stored=False),
    ),
}
# Fetch-based providers come first by design: auth prompts their keys and
# maybe_discover offers them before the slow multi-minute live probes.
PROVIDER_ORDER: tuple[ProviderId, ...] = (
    "openrouter", "nararouter", "opencode", "nim", "gemini",
)


def get_provider(provider_id: str) -> Provider:
    try:
        return PROVIDERS[cast(ProviderId, provider_id)]
    except KeyError:
        raise ToolError(
            f"unknown provider '{provider_id}' (expected: {' '.join(PROVIDER_ORDER)})"
        ) from None


def list_providers() -> list[Provider]:
    """Full provider objects in PROVIDER_ORDER, not just ids."""
    return [PROVIDERS[pid] for pid in PROVIDER_ORDER]
