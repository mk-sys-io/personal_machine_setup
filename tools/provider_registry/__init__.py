"""Shared provider registry + chat adapter.

Public API: get_provider, list_providers, chat_complete.
"""
from .chat import chat_complete
from .providers import (
    PROVIDER_ORDER,
    PROVIDERS,
    ChatConfig,
    Provider,
    ProviderId,
    get_provider,
    list_providers,
)

__all__ = [
    "PROVIDERS",
    "PROVIDER_ORDER",
    "ChatConfig",
    "Provider",
    "ProviderId",
    "chat_complete",
    "get_provider",
    "list_providers",
]
