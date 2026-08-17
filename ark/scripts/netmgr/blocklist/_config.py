"""Shared configuration and validation for the blocklist subpackage.

Gomplated constants stay module-level (baked at deploy by 60-ark.sh).
sources.json + blocklist-custom.txt + blocklist-exclude.txt +
blocklist-exceptions.txt live in both the repo (ARK_REPO_ETC_PATH) and
live (ARK_DATA_PATH) locations; netmgr always edits the repo copy, then
_sync_to_live() pushes to the live dir.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

# -- Constants ----------------------------------------------------------------

# Gomplate-templated constants (rendered from config.env by 60-ark.sh).
DOMAINS_DIR = "{{ .Env.ARK_DATA_PATH }}/domains"
ARK_REPO_ETC_PATH = "{{ .Env.ARK_REPO_ETC_PATH }}"
REPO_DOMAINS_DIR = f"{ARK_REPO_ETC_PATH}/domains/focused"
CUSTOM_FILE = f"{DOMAINS_DIR}/focused/blocklist-custom.txt"
EXCEPTIONS_FILE = f"{DOMAINS_DIR}/focused/blocklist-exceptions.txt"
EXCLUDE_FILE = f"{DOMAINS_DIR}/focused/blocklist-exclude.txt"
SOURCES_FILE = f"{DOMAINS_DIR}/focused/sources.json"
CUSTOM_REPO_FILE = f"{REPO_DOMAINS_DIR}/blocklist-custom.txt"
EXCEPTIONS_REPO_FILE = f"{REPO_DOMAINS_DIR}/blocklist-exceptions.txt"
EXCLUDE_REPO_FILE = f"{REPO_DOMAINS_DIR}/blocklist-exclude.txt"
SOURCES_REPO_FILE = f"{REPO_DOMAINS_DIR}/sources.json"
UPSTREAM_DIR = f"{DOMAINS_DIR}/focused/upstream"
OUTPUT_FORMAT = "{{ .Env.BLOCKLIST_OUTPUT_FORMAT }}"
OUTPUT_FILE = f"{DOMAINS_DIR}/focused/blocklist.{OUTPUT_FORMAT}.conf"
BATCH_SIZE = int("{{ .Env.BLOCKLIST_BATCH_SIZE }}")
DOWNLOAD_TIMEOUT = int("{{ .Env.BLOCKLIST_DOWNLOAD_TIMEOUT }}")
USER_AGENT = "{{ .Env.BLOCKLIST_USER_AGENT }}"

# Headers written on every write (git drops empty files, so a header-only
# file is the valid, tracked, deployable empty state).
CUSTOM_HEADER = "# Custom blocklist -- user-maintained additions\n"
EXCLUDE_HEADER = "# Auto-managed -- domains excluded from generation\n"
EXCEPTIONS_HEADER = "# Auto-managed exemptions -- unblocked from parent wildcards\n"

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


def ensure_upstream_dir() -> None:
    """Create the upstream download directory if it doesn't exist."""
    os.makedirs(UPSTREAM_DIR, exist_ok=True)


def _normalize_line(line: str) -> str:
    """Strip comment prefix and whitespace for domain comparison."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return stripped[1:].strip()
    return stripped


def _read_domains(filepath: str) -> list[str]:
    """Read valid domains from a blocklist file, ignoring comments/blank lines."""
    domains: list[str] = []
    if not os.path.isfile(filepath):
        return domains
    with open(filepath) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and _valid_domain(stripped):
                domains.append(stripped)
    return domains


# -- Repo / live sync ---------------------------------------------------------


def _sync_to_live(rel_path: str) -> None:
    """Copy a repo file to the live location.

    Self-heals a missing live dir (fresh-system safety) and clears any
    immutable flag on the live target. Requires sufficient privileges
    (root / sudo). In focused/locked mode, source-management commands are
    gated by require_unrestricted() and never reach this helper.
    """
    src = f"{REPO_DOMAINS_DIR}/{rel_path}"
    dst = f"{DOMAINS_DIR}/focused/{rel_path}"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    _remove_immutable(dst)
    shutil.copy2(src, dst)


def _repo_file(rel_path: str) -> list[str]:
    """Read the repo copy of a mutable file as lines.

    If the repo copy is missing, adopt the live copy (preserves a
    deploy-seeded state), else seed an empty file. Guarantees the repo
    copy exists afterward. Mutations always operate on the repo copy.
    """
    repo = f"{REPO_DOMAINS_DIR}/{rel_path}"
    live = f"{DOMAINS_DIR}/focused/{rel_path}"
    if not os.path.isfile(repo):
        os.makedirs(REPO_DOMAINS_DIR, exist_ok=True)
        if os.path.isfile(live):
            shutil.copy2(live, repo)
        else:
            with open(repo, "w"):
                pass
    with open(repo) as f:
        return f.readlines()


def _write_repo_file(rel_path: str, lines: list[str]) -> None:
    """Write the repo copy of a mutable file, then sync to live.

    Ownership is enforced here (the single write path for the live line
    files): live copies are root:root — blocklist-custom.txt 640, the rest
    644 — so non-root users can't modify them.
    """
    repo = f"{REPO_DOMAINS_DIR}/{rel_path}"
    os.makedirs(REPO_DOMAINS_DIR, exist_ok=True)
    _remove_immutable(repo)
    with open(repo, "w") as f:
        f.writelines(lines)
    _sync_to_live(rel_path)
    live = f"{DOMAINS_DIR}/focused/{rel_path}"
    os.chown(live, 0, 0)
    os.chmod(live, 0o640 if rel_path == "blocklist-custom.txt" else 0o644)


# -- Exclude file IO ----------------------------------------------------------


def _read_exclude() -> set[str]:
    """Read the exclude file (repo source of truth). Returns empty set if none."""
    result: set[str] = set()
    for line in _repo_file("blocklist-exclude.txt"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            result.add(stripped.lower())
    return result


def _write_exclude(domains: set[str]) -> None:
    """Write the exclude file (repo-first). Always writes a header, never deletes."""
    _write_repo_file(
        "blocklist-exclude.txt",
        [EXCLUDE_HEADER] + [f"{d}\n" for d in sorted(domains)],
    )


# -- Exceptions file IO -------------------------------------------------------


def _read_exceptions() -> list[str]:
    """Read exception domains (repo source of truth). Empty list if none."""
    result: list[str] = []
    for line in _repo_file("blocklist-exceptions.txt"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            result.append(stripped.lower())
    return result


def _write_exceptions(domains: list[str]) -> None:
    """Write the exceptions file (repo-first). Always writes a header."""
    _write_repo_file(
        "blocklist-exceptions.txt",
        [EXCEPTIONS_HEADER] + [f"{d}\n" for d in sorted(set(domains))],
    )


def _remove_immutable(path: str) -> bool:
    """Remove immutable flag if set. Returns True if it was cleared."""
    result = subprocess.run(
        ["chattr", "-i", path],
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0
