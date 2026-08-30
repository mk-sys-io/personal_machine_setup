"""Protocol-aware chat adapter: openai + gemini request/response contracts.

Any consumer (ask.py, the SearXNG plugin) can use any provider — including
gemini — without reimplementing the request/response contract. chat_complete()
dispatches on provider.chat.protocol.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence

from .credentials import get_credentials
from .errors import ToolError
from .providers import Provider

DEFAULT_TIMEOUT = 120  # seconds per chat request
DEFAULT_TEMPERATURE = 0.7
USER_AGENT = "provider-registry/1.0"


def _resolve_key(provider: Provider, key: str | None) -> str:
    if key is not None:
        return key
    return get_credentials(provider.id)


def _chat_openai(
    provider: Provider,
    key: str,
    model: str,
    messages: Sequence[Mapping[str, str]],
    temperature: float,
    timeout: int,
) -> str:
    url = f"{provider.base_url}/chat/completions"
    payload = json.dumps(
        {"model": model, "messages": list(messages), "temperature": temperature}
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": USER_AGENT,
    }
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ToolError(f"chat failed ({provider.id}): HTTP {e.code} {e.reason}") from e
    except OSError as e:
        raise ToolError(f"chat failed ({provider.id}): {e}") from e
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise ToolError(f"chat failed ({provider.id}): unexpected response shape") from e


def _drain_sse_text(body: bytes) -> str:
    """Concatenate text parts from a gemini SSE 'data:' stream."""
    parts: list[str] = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        for cand in obj.get("candidates", []):
            content = cand.get("content") or {}
            for part in content.get("parts", []):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts).strip()


def _chat_gemini(
    provider: Provider,
    key: str,
    model: str,
    messages: Sequence[Mapping[str, str]],
    temperature: float,
    timeout: int,
) -> str:
    url = f"{provider.base_url}/models/{model}:generateContent?alt=sse"
    # Gemini has no 'system' role in contents — fold system prompts into user.
    contents = [
        {
            "role": "user" if m.get("role") == "system" else m.get("role", "user"),
            "parts": [{"text": m.get("content", "")}],
        }
        for m in messages
    ]
    payload = json.dumps(
        {"contents": contents, "generationConfig": {"temperature": temperature}}
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": key,
        "User-Agent": USER_AGENT,
    }
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise ToolError(f"chat failed ({provider.id}): HTTP {e.code} {e.reason}") from e
    except OSError as e:
        raise ToolError(f"chat failed ({provider.id}): {e}") from e
    text = _drain_sse_text(body)
    if not text:
        raise ToolError(f"chat failed ({provider.id}): empty response")
    return text


def chat_complete(
    provider: Provider,
    messages: Sequence[Mapping[str, str]],
    *,
    key: str | None = None,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Protocol-aware chat request; dispatches on provider.chat.protocol."""
    if provider.chat is None:
        raise ToolError(
            f"provider '{provider.id}' is not chat-capable (no ChatConfig)"
        )
    resolved_key = _resolve_key(provider, key)
    resolved_model = model or provider.chat.default_model
    if provider.chat.protocol == "gemini":
        return _chat_gemini(
            provider, resolved_key, resolved_model, messages, temperature, timeout
        )
    return _chat_openai(
        provider, resolved_key, resolved_model, messages, temperature, timeout
    )
