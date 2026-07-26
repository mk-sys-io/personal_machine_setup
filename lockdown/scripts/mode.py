#!/usr/bin/env python3
"""Mode state storage — single source of truth for lockdown mode.

Usage (Python):
    from mode import ensure, read, write

Usage (Bash):
    python3 @LOCKDOWN_DATA_PATH@/scripts/mode.py read
    python3 @LOCKDOWN_DATA_PATH@/scripts/mode.py write "focused"

Modes: unrestricted, focused, locked
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MODE_FILE = "@LOCKDOWN_DATA_PATH@/mode"
VALID_MODES = ("unrestricted", "focused", "locked")


def ensure() -> None:
    """Create mode file if missing. Enforce root:root 644 ownership."""
    path = Path(MODE_FILE)
    if not path.exists():
        path.write_text("unrestricted\n")
    _set_immutable(MODE_FILE, False)
    os.chown(MODE_FILE, 0, 0)
    os.chmod(MODE_FILE, 0o644)
    _set_immutable(MODE_FILE, True)


def read() -> str:
    """Read current mode. Returns 'unrestricted' if file absent."""
    if not os.path.isfile(MODE_FILE):
        return "unrestricted"
    return Path(MODE_FILE).read_text().strip()


def write(mode: str) -> None:
    """The only way to change mode. Atomic write + immutable flag management."""
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode: {mode}")
    _set_immutable(MODE_FILE, False)
    tmp = Path(f"{MODE_FILE}.new")
    tmp.write_text(f"{mode}\n")
    tmp.rename(MODE_FILE)
    _set_immutable(MODE_FILE, True)


def _set_immutable(path: str, immutable: bool) -> None:
    """Toggle immutable flag via chattr. +i if True, -i if False."""
    flag = "+i" if immutable else "-i"
    try:
        subprocess.run(["chattr", flag, path], check=True,
                       capture_output=True)
    except FileNotFoundError:
        print("Warning: chattr not found, skipping immutable flag",
              file=sys.stderr)
    except subprocess.CalledProcessError:
        print(f"Warning: failed to set immutable flag on {path}",
              file=sys.stderr)


def _cli() -> None:
    if len(sys.argv) < 2:
        print("Usage: mode.py read|write <mode>", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "read":
        print(read())
    elif command == "write":
        if len(sys.argv) < 3:
            print("Usage: mode.py write <mode>", file=sys.stderr)
            sys.exit(1)
        write(sys.argv[2])
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
