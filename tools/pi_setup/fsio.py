"""Filesystem + auth.json access layer: atomic writes, backups, key lookup."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import cast

from .config import AUTH_JSON, CURATED_DIR
from .errors import AuthParseError, ToolError
from .providers import PROVIDERS, ProviderId


def warn_lock() -> None:
    lock = Path(str(AUTH_JSON) + ".lock")
    if lock.exists():
        print(
            f"pi: WARNING: {lock} exists (Pi may be running or a stale lock)",
            file=sys.stderr,
        )


def load_auth() -> dict[str, object]:
    try:
        with AUTH_JSON.open() as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        raise AuthParseError(f"cannot parse {AUTH_JSON}") from e
    if not isinstance(data, dict):
        raise AuthParseError(f"cannot parse {AUTH_JSON}")
    return cast(dict[str, object], data)


def entry_exists(data: dict[str, object], prov: ProviderId) -> bool:
    return bool(data.get(prov))


def require_key(prov: ProviderId) -> str:
    """Resolve a provider's API key from the vault (single source of truth).

    Re-wraps the shared module's ToolError as pi_setup's so callers that
    catch pi_setup.errors.ToolError (probe.py/catalog.py) handle it.
    """
    from provider_registry.credentials import get_credentials
    from provider_registry.errors import ToolError as RegistryToolError
    try:
        return get_credentials(prov)
    except RegistryToolError as e:
        raise ToolError(str(e)) from e


def atomic_write(path: Path, content: str, mode: int) -> None:
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.tmp.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
        os.chmod(path, mode)
    except Exception:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        raise


def backup_file(path: Path) -> Path | None:
    """Copy path to path.bak (0600). Returns the backup path, or None if absent."""
    if not path.exists():
        return None
    backup = Path(str(path) + ".bak")
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    return backup


def wipe_json(path: Path) -> None:
    """Replace path with an empty JSON object (0600)."""
    atomic_write(path, "{}\n", 0o600)


def curated_file(prov: ProviderId) -> Path:
    p = PROVIDERS[prov]
    if p.probe is None:
        raise ToolError(f"provider '{prov}' has no probe config")
    return CURATED_DIR / p.probe.curated_file


def write_curated(path: Path, data: dict[str, object], *, fail_verb: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=2) + "\n", 0o644)
    except OSError as e:
        raise ToolError(f"failed to {fail_verb} {path}") from e
