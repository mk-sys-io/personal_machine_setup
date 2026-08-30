"""Storage-abstracted credential store: vault.json (0600, atomic).

The vault holds per-provider API-key credentials AND the probe/fetch strategy
that provider_registry manages. It does NOT share a file with Pi's auth.json
(deployment target), so there are no /login OAuth entries here.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, cast

from .config import VAULT_JSON
from .errors import AuthParseError, ToolError


class CredentialStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self) -> list[str]: ...


class JsonCredentialStore:
    """Reads/writes vault.json atomically (0600).

    Nested format: {provider_id: {"type": "api_key", "key": ...,
    "strategy": "probe"|"fetch"}}. The strategy field is pi_setup-internal
    and is NOT copied to Pi's auth.json (see the copy flow).
    """

    def __init__(self, path: Path = VAULT_JSON) -> None:
        self.path = path

    def _load(self) -> dict[str, dict[str, object]]:
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as e:
            raise AuthParseError(f"cannot parse {self.path}") from e
        if not isinstance(data, dict):
            raise AuthParseError(f"cannot parse {self.path}")
        return cast(dict[str, dict[str, object]], data)

    def _write(self, data: dict[str, dict[str, object]]) -> None:
        tmp_name = ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f"{self.path.name}.tmp."
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2) + "\n")
            os.replace(tmp_name, self.path)
            os.chmod(self.path, 0o600)
        except OSError as e:
            raise ToolError(f"failed to write {self.path}") from e
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass

    def get(self, key: str) -> str | None:
        entry = self._load().get(key)
        if isinstance(entry, dict):
            val = entry.get("key")
            if isinstance(val, str) and val:
                return val
        return None

    def set(self, key: str, value: str, *, strategy: str | None = None) -> None:
        data = self._load()
        entry: dict[str, object] = {"type": "api_key", "key": value}
        if strategy is not None:
            entry["strategy"] = strategy
        data[key] = entry
        self._write(dict(sorted(data.items())))

    def delete(self, key: str) -> None:
        data = self._load()
        data.pop(key, None)
        self._write(dict(sorted(data.items())))

    def keys(self) -> list[str]:
        return sorted(self._load())

    def strategy(self, key: str) -> str | None:
        entry = self._load().get(key)
        if isinstance(entry, dict):
            val = entry.get("strategy")
            if isinstance(val, str):
                return val
        return None


def get_credentials(provider_id: str) -> str:
    """Resolve a provider's API key from vault.json (single source of truth).

    No env-var fallback. Returns raw values (may be '!cmd'/'$ENV' refs);
    consumers that need a real key detect and handle those.
    """
    store = JsonCredentialStore()
    key = store.get(provider_id)
    if key is None:
        raise ToolError(
            f"no API key for provider '{provider_id}' in {VAULT_JSON}"
            f" — run: provider-registry add {provider_id}"
        )
    return key
