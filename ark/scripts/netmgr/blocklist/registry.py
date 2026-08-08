"""Registry + exclude-file IO for the blocklist subpackage.

Split from netmgr/blocklist.py (P13). Zero coupling to the manager functions;
the registry is write-hot and is never immutable-flagged (a stale +i flag from
an old deployment is cleared best-effort on write).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
from typing import TypedDict

from ._config import (
    DOMAINS_DIR,
    EXCLUDE_FILE,
    REGISTRY_FILE,
    BlocklistError,
    ensure_domains_dir,
)


class RegistryEntry(TypedDict):
    id: str
    url: str
    file: str
    checksum: str


def _read_exclude() -> set[str]:
    """Read the exclude file. Returns empty set if missing."""
    if not os.path.isfile(EXCLUDE_FILE):
        return set()
    result: set[str] = set()
    with open(EXCLUDE_FILE) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                result.add(stripped.lower().strip())
    return result


def _write_exclude(domains: set[str]) -> None:
    """Write the exclude file. Removes the file if empty."""
    if not domains:
        if os.path.isfile(EXCLUDE_FILE):
            remove_immutable(EXCLUDE_FILE)
            os.remove(EXCLUDE_FILE)
        return
    ensure_domains_dir()
    remove_immutable(EXCLUDE_FILE)
    with open(EXCLUDE_FILE, "w") as f:
        f.write("# Auto-maintained — domains excluded from generation\n")
        for d in sorted(domains):
            f.write(f"{d}\n")
    os.chown(EXCLUDE_FILE, 0, 0)
    os.chmod(EXCLUDE_FILE, 0o644)


def load_registry() -> list[RegistryEntry]:
    """Load the registry from disk."""
    if not os.path.isfile(REGISTRY_FILE):
        return []
    with open(REGISTRY_FILE) as f:
        return json.load(f)


def remove_immutable(path: str) -> bool:
    """Remove immutable flag. Returns True if it was set."""
    result = subprocess.run(
        ["chattr", "-i", path],
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def update_registry(entries: list[RegistryEntry]) -> None:
    """Write registry with corruption protection. No immutable flag.

    The registry was previously +i'd; a stale flag from an old deployment is
    cleared here (best-effort) and never re-applied — the write-hot file must
    not toggle the flag on every registry write.
    """
    remove_immutable(REGISTRY_FILE)

    backup: str | None = None
    if os.path.isfile(REGISTRY_FILE):
        backup = REGISTRY_FILE + ".bak"
        shutil.copy2(REGISTRY_FILE, backup)

    try:
        with open(REGISTRY_FILE, "w") as f:
            json.dump(entries, f, indent=2)
            f.write("\n")
        os.chown(REGISTRY_FILE, 0, 0)
        os.chmod(REGISTRY_FILE, 0o644)

        with open(REGISTRY_FILE) as f:
            json.load(f)
    except Exception as e:
        if backup and os.path.isfile(backup):
            shutil.move(backup, REGISTRY_FILE)
        raise BlocklistError(f"Error updating registry: {e}") from e
    finally:
        if backup and os.path.isfile(backup):
            remove_immutable(backup)
            os.remove(backup)


def sync_registry() -> list[RegistryEntry]:
    """Reconcile registry against actual files on disk."""
    entries = load_registry()
    if not entries:
        return entries

    actual_files: set[str] = {
        os.path.basename(f)
        for f in glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt"))
    }

    synced: list[RegistryEntry] = []
    changed: bool = False
    for entry in entries:
        if entry["file"] in actual_files:
            synced.append(entry)
        else:
            changed = True

    if changed:
        update_registry(synced)

    return synced


def init_registry() -> None:
    """Create registry if it doesn't exist. No immutable flag (write-hot file)."""
    if os.path.isfile(REGISTRY_FILE):
        return
    ensure_domains_dir()
    update_registry([])


def sha256_file(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
