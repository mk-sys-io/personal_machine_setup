"""Blocklist subpackage — facade re-exporting the blocklist public API.

Split from netmgr/blocklist.py (P13) for navigability. The public surface is
unchanged: ``from netmgr.blocklist import ...`` resolves the exact same names
as before, so the netmgr CLI (P8) and ark.py (P9) imports are byte-identical.
The split is purely structural — no behavior change.
"""
from __future__ import annotations

from ._config import (
    BATCH_SIZE,
    CUSTOM_FILE,
    DOMAIN_RE,
    DOMAINS_DIR,
    DOWNLOAD_TIMEOUT,
    EXCEPTIONS_FILE,
    EXCLUDE_FILE,
    MAX_DOMAIN_LEN,
    OUTPUT_FILE,
    OUTPUT_FORMAT,
    REGISTRY_FILE,
    USER_AGENT,
    BlocklistError,
    ensure_domains_dir,
)
from .download import download, extract_zip, normalize_url
from .generate import generate
from .manage import add, purge, search, stats, toggle, verify
from .registry import (
    RegistryEntry,
    init_registry,
    load_registry,
    remove_immutable,
    sha256_file,
    sync_registry,
    update_registry,
)

__all__ = [
    "BATCH_SIZE",
    "BlocklistError",
    "CUSTOM_FILE",
    "DOMAIN_RE",
    "DOMAINS_DIR",
    "DOWNLOAD_TIMEOUT",
    "EXCEPTIONS_FILE",
    "EXCLUDE_FILE",
    "MAX_DOMAIN_LEN",
    "OUTPUT_FILE",
    "OUTPUT_FORMAT",
    "REGISTRY_FILE",
    "RegistryEntry",
    "USER_AGENT",
    "add",
    "download",
    "ensure_domains_dir",
    "extract_zip",
    "generate",
    "init_registry",
    "load_registry",
    "normalize_url",
    "purge",
    "remove_immutable",
    "search",
    "sha256_file",
    "stats",
    "sync_registry",
    "toggle",
    "update_registry",
    "verify",
]
