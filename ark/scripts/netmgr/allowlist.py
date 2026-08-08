"""Allowlist read (any mode) + gated edit (unrestricted only).

The allowlist CLI fold (P14): the legacy shell-managed allowlist operations
become a netmgr subgroup. Read operations (list/search/status counts) work in
ANY mode — including locked — via NOPASSWD sudoers grants; edit operations
(add/remove/clear-session) require unrestricted mode exclusively
(guards.require_unrestricted() gate — focused is blocked even with password
sudo, locked has no sudo at all). Mode transitions are ark-only; editing is
data-only — unrestricted DNS doesn't enforce the allowlist, so no redeploy on
edit; ark applies the current files at the next transition.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import opslog

from .guards import require_unrestricted

# Gomplate-templated constant
LOCKED_DIR = "{{ .Env.ARK_DATA_PATH }}/domains/locked"

SECTIONS: dict[str, str] = {
    "infra": f"{LOCKED_DIR}/infra.txt",
    "base": f"{LOCKED_DIR}/base.txt",
    "session": f"{LOCKED_DIR}/session.txt",
}

# Mirrors blocklist._config.DOMAIN_RE — self-contained (no cross-module coupling).
DOMAIN_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)
MAX_DOMAIN_LEN: int = 253


class AllowlistError(Exception):
    """Raised when an allowlist operation fails."""


def _valid_domain(d: str) -> bool:
    """Check if a string is a valid domain name (mirrors blocklist)."""
    return bool(d) and len(d) <= MAX_DOMAIN_LEN and DOMAIN_RE.match(d) is not None


def _section_file(section: str) -> str:
    if section not in SECTIONS:
        raise AllowlistError(
            f"unknown section: {section} (expected infra, base, or session)"
        )
    return SECTIONS[section]


def _read_lines(path: str) -> list[str]:
    try:
        with open(path) as f:
            return f.readlines()
    except FileNotFoundError:
        return []


def _write(path: str, lines: list[str]) -> None:
    """Atomic write preserving root:root 0640 ownership (deploy parity)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text("".join(lines))
    tmp.rename(target)
    os.chmod(target, 0o640)
    if os.geteuid() == 0:
        os.chown(target, 0, 0)


def counts() -> dict[str, int]:
    """Per-section domain counts; graceful 0 when unprivileged (files are 640)."""
    result: dict[str, int] = {}
    for name in ("infra", "base", "session"):
        result[name] = 0
        try:
            with open(SECTIONS[name]) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        result[name] += 1
        except (FileNotFoundError, PermissionError, OSError):
            pass
    return result


# ── Read (any mode) ───────────────────────────────────────────────────────────

def list_sections(section: str | None = None) -> None:
    """List allowlist domains with per-section headers."""
    for name in ("infra", "base", "session"):
        if section and name != section:
            continue
        opslog.info("=== %s ===", name.upper())
        for line in _read_lines(SECTIONS[name]):
            opslog.info("%s", line.rstrip("\n"))
        opslog.info("")


def search(pattern: str) -> None:
    """Search allowlist domains, `[section] line` output."""
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise AllowlistError(f"Invalid regex: {e}") from e

    found = 0
    for name in ("infra", "base", "session"):
        for line in _read_lines(SECTIONS[name]):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if compiled.search(stripped):
                opslog.info("[%s] %s", name, stripped)
                found += 1
    if not found:
        opslog.info("No matches for: %s", pattern)


# ── Write (unrestricted only) ─────────────────────────────────────────────────

def add(section: str, domains: list[str]) -> None:
    """Add domain(s) to an allowlist section (lower/strip, validate, dedupe)."""
    require_unrestricted()
    if not domains:
        raise AllowlistError("no domains given")
    path = _section_file(section)
    clean = [d.lower().strip() for d in domains]
    for d in clean:
        if not _valid_domain(d):
            raise AllowlistError(f"Invalid domain format: {d}")

    lines = _read_lines(path)
    existing = {
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }
    added: list[str] = []
    for d in clean:
        if d in existing:
            opslog.info("Already in %s allowlist: %s", section, d)
            continue
        lines.append(f"{d}\n")
        existing.add(d)
        added.append(d)

    if not added:
        return
    _write(path, lines)
    for d in added:
        opslog.info("Added to %s allowlist: %s", section, d)


def remove(section: str, domains: list[str]) -> None:
    """Remove domain(s) from an allowlist section (exact normalized match)."""
    require_unrestricted()
    if not domains:
        raise AllowlistError("no domains given")
    path = _section_file(section)
    targets = {d.lower().strip() for d in domains}
    lines = _read_lines(path)
    kept: list[str] = []
    removed: list[str] = []
    for line in lines:
        stripped = line.strip()
        norm = stripped[1:].strip() if stripped.startswith("#") else stripped
        if norm in targets:
            removed.append(norm)
        else:
            kept.append(line)
    if not removed:
        opslog.info(
            "No matches in %s allowlist: %s", section, ", ".join(sorted(targets))
        )
        return
    _write(path, kept)
    for d in sorted(set(removed)):
        opslog.info("Removed from %s allowlist: %s", section, d)


def clear_session() -> None:
    """Clear the session allowlist. Data-only — no redeploy (ark applies at the
    next transition; unrestricted DNS doesn't enforce the allowlist)."""
    require_unrestricted()
    _write(SECTIONS["session"], [])
    opslog.info("Cleared: session domains")
