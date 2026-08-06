#!/usr/bin/env python3
"""Immutable-flag management for ark (single home for chattr +i/-i).

Every chattr caller in the codebase (mode.py, cask_lib.py, ark.py, deploy)
goes through this module so flag handling is uniform:
fail-loud on clear, verify-after-set with one retry, and crash-safe
self-heal via ``*.old`` promotion.

Root-only: all ark flows that touch flags run as root (ark, mcask, cask_system
are NOPASSWD-root; cask_system.py enforces ``os.geteuid() == 0``). ``lsattr``
reads work for unprivileged paths, but set/clear/repair are checked.

Usage (Bash):
    python3 /opt/ark/scripts/immutable_lib.py is PATH     # status
    python3 /opt/ark/scripts/immutable_lib.py set PATH
    python3 /opt/ark/scripts/immutable_lib.py clear PATH
    python3 /opt/ark/scripts/immutable_lib.py repair PATH..
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

IMMUTABLE_INDEX = 4  # lsattr flags field: index 4 is 'i' (e.g. ----i--------e-------)


class ImmutableError(RuntimeError):
    """Raised when a chattr operation fails or cannot be verified."""


def require_root() -> None:
    if os.geteuid() != 0:
        raise ImmutableError(
            "immutable flag management must be run as root (sudo) — "
            + f"got euid {os.geteuid()}"
        )

def check_available() -> None:
    """Fail with an actionable message if chattr/lsattr are missing."""
    missing = [t for t in ("chattr", "lsattr") if shutil.which(t) is None]
    if missing:
        raise ImmutableError(
            f"required tools not found: {', '.join(missing)} — install e2fsprogs"
        )


def flag_status(path: str) -> str:
    """Return 'immutable', 'clear', 'missing', or 'unknown'."""
    if not os.path.lexists(path):
        return "missing"
    try:
        res = subprocess.run(["lsattr", "--", path], capture_output=True, text=True, check=False)
    except OSError:
        return "unknown"
    if res.returncode != 0:
        return "unknown"
    fields = res.stdout.split()
    if not fields:
        return "unknown"
    flags = fields[0]
    if len(flags) <= IMMUTABLE_INDEX:
        return "clear"
    return "immutable" if flags[IMMUTABLE_INDEX] == "i" else "clear"


def is_immutable(path: str) -> bool:
    return flag_status(path) == "immutable"


def _verify_readable(path: str) -> None:
    status = flag_status(path)
    if status == "unknown":
        check_available()
        raise ImmutableError(f"could not read immutable flag on {path}")


def set_immutable(path: str) -> None:
    """Apply +i and verify. One retry after a reported-success verify failure."""
    require_root()
    status = flag_status(path)
    if status == "immutable":
        return
    if status == "missing":
        raise ImmutableError(f"cannot set immutable flag on missing file: {path}")
    if status == "unknown":
        _verify_readable(path)
    for attempt in (1, 2):
        res = subprocess.run(["chattr", "+i", path], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            if attempt == 2:
                raise ImmutableError(
                    f"chattr +i failed on {path}: {res.stderr.strip()}"
                )
            continue
        if is_immutable(path):
            return
        if attempt == 2:
            raise ImmutableError(
                f"chattr +i reported success but flag not set on {path}"
            )
    raise ImmutableError(f"could not set immutable flag on {path}")


def clear_immutable(path: str, strict: bool = True) -> None:
    """Remove +i. Missing or already-clear files are a no-op.

    With strict=True (default) a failed clear raises so callers can abort
    before mutating a protected file.
    """
    require_root()
    status = flag_status(path)
    if status in ("missing", "clear"):
        return
    if status == "unknown":
        _verify_readable(path)
    res = subprocess.run(["chattr", "-i", path], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        if strict:
            raise ImmutableError(
                f"chattr -i failed on {path}: {res.stderr.strip()}"
            )
        return
    if is_immutable(path) and strict:
        raise ImmutableError(f"chattr -i reported success but flag still set on {path}")


def repair_immutable(paths: list[str]) -> tuple[list[str], list[str]]:
    """Self-heal crash-interrupted write->+i sequences.

    Returns (repaired, failed). For each path:
      - path exists + immutable   -> drop stale ``path.old``
      - path exists + unprotected -> re-apply +i, drop stale ``path.old``
      - path missing, .old exists -> promote .old, then +i
    """
    require_root()
    repaired: list[str] = []
    failed: list[str] = []
    for path in paths:
        old = f"{path}.old"
        try:
            if os.path.lexists(path):
                if is_immutable(path):
                    if os.path.lexists(old):
                        clear_immutable(old)
                        os.remove(old)
                    continue
                set_immutable(path)
                if os.path.lexists(old):
                    clear_immutable(old)
                    os.remove(old)
                repaired.append(path)
                continue
            if os.path.lexists(old):
                clear_immutable(old)
                os.replace(old, path)
                set_immutable(path)
                repaired.append(path)
                continue
        except (ImmutableError, OSError) as e:
            failed.append(f"{path}: {e}")
    return repaired, failed


def _cli() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: immutable_lib.py is|set|clear|repair PATH...",
            file=sys.stderr,
        )
        sys.exit(2)

    command = sys.argv[1]
    paths = sys.argv[2:]

    if command == "is":
        try:
            status = flag_status(paths[0])
        except OSError:
            status = "unknown"
        label = {
            "immutable": "IMMUTABLE",
            "clear": "NOT-IMMUTABLE",
            "missing": "NOT-IMMUTABLE",
            "unknown": "UNKNOWN",
        }[status]
        print(label)
        sys.exit(0 if status == "immutable" else 2 if status == "unknown" else 1)
    elif command in ("set", "clear"):
        try:
            if command == "set":
                set_immutable(paths[0])
            else:
                clear_immutable(paths[0])
        except ImmutableError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    elif command == "repair":
        try:
            repaired, failed = repair_immutable(paths)
        except ImmutableError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        for path in repaired:
            print(f"repaired: {path}")
        for entry in failed:
            print(f"FAILED: {entry}", file=sys.stderr)
        if failed:
            sys.exit(1)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    _cli()
