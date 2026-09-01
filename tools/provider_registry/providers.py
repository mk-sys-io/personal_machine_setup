"""Provider registry: identity, endpoints, chat contract (shared).

Composition: a core Provider (fields used by all consumers) plus an optional
ChatConfig for chat consumers (ask.py, SearXNG plugin). Pi-specific concerns
(probe/fetch strategy, curated files, Pi flags) live in pi_setup.providers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .errors import ToolError

ProviderId = Literal["openrouter", "nararouter", "opencode", "nim", "gemini"]
Protocol = Literal["openai", "gemini"]


@dataclass(frozen=True)
class ChatConfig:
    """Request/response contract for chat consumers (ask.py, SearXNG plugin)."""

    protocol: Protocol
    model_env: str
    default_model: str


@dataclass(frozen=True)
class Provider:
    id: ProviderId
    label: str
    env_var: str
    base_url: str
    chat: ChatConfig | None = None  # ask.py + plugin (2 consumers)


# id|label|env var|base URL|chat
# Protocol: gemini = native :generateContent, others = OpenAI-compatible
# /chat/completions. default_model values are the first (fastest) curated
# model per provider.
PROVIDERS: dict[ProviderId, Provider] = {
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter (free)",
        env_var="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="OPENROUTER_MODEL",
            default_model="cohere/north-mini-code:free",
        ),
    ),
    "nararouter": Provider(
        id="nararouter",
        label="NaraRouter (free)",
        env_var="NARAROUTER_API_KEY",
        base_url="https://router.bynara.id/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="NARAROUTER_MODEL",
            default_model="agnes-2.0-flash",
        ),
    ),
    "opencode": Provider(
        id="opencode",
        label="OpenCode Zen",
        env_var="OPENCODE_API_KEY",
        base_url="https://opencode.ai/zen/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="OPENCODE_MODEL",
            default_model="big-pickle",
        ),
    ),
    "nim": Provider(
        id="nim",
        label="NVIDIA NIM (live)",
        env_var="NVIDIA_NIM_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
        chat=ChatConfig(
            protocol="openai",
            model_env="NIM_MODEL",
            default_model="meta/llama-3.1-70b-instruct",
        ),
    ),
    "gemini": Provider(
        id="gemini",
        label="Google AI Studio (Gemini)",
        env_var="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        chat=ChatConfig(
            protocol="gemini",
            model_env="GEMINI_MODEL",
            default_model="gemini-3-flash-preview",
        ),
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
