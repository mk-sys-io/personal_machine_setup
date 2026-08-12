"""Shared configuration and validation for the blocklist subpackage.

Split from netmgr/blocklist.py (P13). Gomplated constants stay module-level
(baked at deploy by 60-ark.sh, same as before the split).
"""
from __future__ import annotations

import os
import re

# ── Constants ─────────────────────────────────────────────────────────────────

# Gomplate-templated constants (rendered from config.env by 60-ark.sh).
# P6: output is the dnsmasq format file (blocklist.dnsmasq.conf); the custom
# list lives in focused/; the registry/exclude/downloads stay at the root.
DOMAINS_DIR = "{{ .Env.ARK_DATA_PATH }}/domains"
REGISTRY_FILE = f"{DOMAINS_DIR}/.blocklist-registry.json"
CUSTOM_FILE = f"{DOMAINS_DIR}/focused/blocklist-custom.txt"
EXCEPTIONS_FILE = f"{DOMAINS_DIR}/focused/blocklist-exceptions.txt"
EXCLUDE_FILE = f"{DOMAINS_DIR}/blocklist-exclude.txt"
OUTPUT_FORMAT = "{{ .Env.BLOCKLIST_OUTPUT_FORMAT }}"
OUTPUT_FILE = f"{DOMAINS_DIR}/blocklist.{OUTPUT_FORMAT}.conf"
BATCH_SIZE = int("{{ .Env.BLOCKLIST_BATCH_SIZE }}")
DOWNLOAD_TIMEOUT = int("{{ .Env.BLOCKLIST_DOWNLOAD_TIMEOUT }}")
USER_AGENT = "{{ .Env.BLOCKLIST_USER_AGENT }}"

DOMAIN_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)
MAX_DOMAIN_LEN: int = 253


class BlocklistError(Exception):
    """Raised when a blocklist operation fails."""


def _valid_domain(d: str) -> bool:
    """Check if a string is a valid domain name."""
    return bool(d) and len(d) <= MAX_DOMAIN_LEN and DOMAIN_RE.match(d) is not None


def ensure_domains_dir() -> None:
    """Create the domains directory if it doesn't exist."""
    os.makedirs(DOMAINS_DIR, exist_ok=True)


def _normalize_line(line: str) -> str:
    """Strip comment prefix and whitespace for domain comparison."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return stripped[1:].strip()
    return stripped
