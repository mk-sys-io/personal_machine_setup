#!/usr/bin/env python3
"""Merge the Brave NTP preferences seed into the live profile.

Brave writes Preferences on exit, so a merge while Brave is running would be
silently overwritten — this module terminates Brave gracefully first
(SIGTERM → poll for full exit → SIGKILL stragglers), then merges.

Run standalone: python3 lib/45-brave.py
Wired into install.sh as module 45.

Exit codes:
    0  merged
    1  failure (Brave not installed, seed missing, merge/verify failed)
    2  skipped (no Preferences file yet — Brave never run)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED = REPO_ROOT / "dotfiles" / "browsers" / "brave" / "preferences.seed.json"
PREFS = Path.home() / ".config" / "BraveSoftware" / "Brave-Browser" / "Default" / "Preferences"

KILL_POLL_SECONDS = 10
KILL_POLL_INTERVAL = 0.5


def brave_running() -> bool:
    return subprocess.run(["pgrep", "-x", "brave"], capture_output=True).returncode == 0


def terminate_brave() -> None:
    if not brave_running():
        return
    print("brave-preferences: Brave running — terminating gracefully")
    subprocess.run(["pkill", "-TERM", "-x", "brave"], check=False)
    waited = 0.0
    while waited < KILL_POLL_SECONDS:
        if not brave_running():
            return
        time.sleep(KILL_POLL_INTERVAL)
        waited += KILL_POLL_INTERVAL
    print("brave-preferences: Brave did not exit — forcing kill")
    subprocess.run(["pkill", "-KILL", "-x", "brave"], check=False)
    time.sleep(1)


def deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value


def verify_seed(base: dict, overlay: dict, path: str = "") -> None:
    for key, value in overlay.items():
        current_path = f"{path}.{key}" if path else key
        if isinstance(value, dict):
            if not isinstance(base.get(key), dict):
                raise ValueError(f"{current_path} missing or wrong type")
            verify_seed(base[key], value, current_path)
        elif base.get(key) != value:
            raise ValueError(f"{current_path} = {base.get(key)!r}, expected {value!r}")


def write_prefs(prefs: dict, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=PREFS.parent, prefix=".Preferences.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, PREFS)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if shutil.which("brave-browser") is None:
        print("brave-preferences: ERROR Brave not installed (brave-browser not in PATH)", file=sys.stderr)
        return 1

    if not PREFS.exists():
        print(f"brave-preferences: no Preferences file at {PREFS} — skipping")
        return 2

    if not SEED.exists():
        print(f"brave-preferences: ERROR seed missing: {SEED}", file=sys.stderr)
        return 1

    terminate_brave()

    try:
        backup = f"{PREFS}.bak-{int(time.time())}"
        shutil.copy2(PREFS, backup)
        print(f"brave-preferences: backed up to {backup}")

        with open(SEED, encoding="utf-8") as f:
            seed = json.load(f)
        with open(PREFS, encoding="utf-8") as f:
            prefs = json.load(f)
        mode = os.stat(PREFS).st_mode

        deep_merge(prefs, seed)
        write_prefs(prefs, mode)

        with open(PREFS, encoding="utf-8") as f:
            merged = json.load(f)
        verify_seed(merged, seed)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"brave-preferences: ERROR {e}", file=sys.stderr)
        return 1

    print("brave-preferences: merged and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
