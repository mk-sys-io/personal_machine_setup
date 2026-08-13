#!/usr/bin/env python3
"""Mode state storage — single source of truth for lockdown mode.

Usage (Python):
    from mode import ensure, read, write

Usage (Bash):
    python3 {{ .Env.ARK_DATA_PATH }}/scripts/mode.py read
    python3 {{ .Env.ARK_DATA_PATH }}/scripts/mode.py write "focused"

Modes: unrestricted, focused, locked
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import immutable_lib

MODE_FILE = "{{ .Env.ARK_DATA_PATH }}/mode"
VALID_MODES = ("unrestricted", "focused", "locked")


class ModeError(RuntimeError):
    """Raised when the mode state is missing or corrupt (fail-closed read).

    Carry the message verbatim — it doubles as the user-facing recovery hint
    (deploy / mode.ensure()). read() never assumes a mode on bad state.
    """


def ensure() -> None:
    """Create mode file if missing. Enforce root:root 644 + immutable flag."""
    immutable_lib.check_available()
    _ = immutable_lib.repair_immutable([MODE_FILE])
    path = Path(MODE_FILE)
    if not path.exists():
        _ = path.write_text("unrestricted\n")
    immutable_lib.clear_immutable(MODE_FILE)
    _ = os.chown(MODE_FILE, 0, 0)
    os.chmod(MODE_FILE, 0o644)
    immutable_lib.set_immutable(MODE_FILE)


def read() -> str:
    """Read current mode. Fail-closed: missing or corrupt state raises
    ModeError (deploy / mode.ensure() hint) — never an assumed mode."""
    if not os.path.isfile(MODE_FILE):
        raise ModeError(
            f"mode state missing: {MODE_FILE}\n"
            "  Run: sudo install.sh (deploy bootstraps /opt/ark/mode) or "
            "mode.ensure()"
        )
    try:
        value = Path(MODE_FILE).read_text().strip()
    except OSError as e:
        raise ModeError(f"cannot read mode state {MODE_FILE}: {e}") from e
    if value not in VALID_MODES:
        raise ModeError(
            f"mode state corrupt: {MODE_FILE} contains {value!r}\n"
            "  Expected one of: " + ", ".join(VALID_MODES) + "\n"
            "  Run: mode.ensure() or sudo install.sh"
        )
    return value


def write(mode: str) -> None:
    """The only way to change mode. Crash-safe atomic write + immutable flag."""
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode: {mode}")
    immutable_lib.check_available()
    _ = immutable_lib.repair_immutable([MODE_FILE])
    immutable_lib.clear_immutable(MODE_FILE)
    tmp = Path(f"{MODE_FILE}.new")
    with tmp.open("w") as fh:
        _ = fh.write(f"{mode}\n")
        fh.flush()
        _ = os.fsync(fh.fileno())
    if os.path.isfile(MODE_FILE):
        os.replace(MODE_FILE, f"{MODE_FILE}.old")
    os.replace(tmp, MODE_FILE)
    try:
        immutable_lib.set_immutable(MODE_FILE)
    except immutable_lib.ImmutableError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(f"  Mode written to {MODE_FILE} but not immutable — "
              + "repair re-applies the flag on the next run.", file=sys.stderr)
    old = Path(f"{MODE_FILE}.old")
    if old.is_file():
        try:
            immutable_lib.clear_immutable(str(old))
            old.unlink()
        except immutable_lib.ImmutableError as e:
            print(f"Warning: could not remove stale {old}: {e}", file=sys.stderr)


def _cli() -> None:
    if len(sys.argv) < 2:
        print("Usage: mode.py read|write <mode>", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "read":
        try:
            print(read())
        except ModeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
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
