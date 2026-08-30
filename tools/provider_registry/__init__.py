"""Shared provider registry + credential store.

Public API: get_provider, get_credentials, list_providers, chat_complete.
"""
from .chat import chat_complete
from .credentials import get_credentials
from .providers import (
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

__all__ = [
    "PROVIDERS",
    "PROVIDER_ORDER",
    "ChatConfig",
    "PiConfig",
    "ProbeConfig",
    "Provider",
    "ProviderId",
    "chat_complete",
    "get_credentials",
    "get_provider",
    "list_providers",
]
