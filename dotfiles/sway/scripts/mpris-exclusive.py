#!/usr/bin/env python3
"""
mpris-exclusive.py — MPRIS mutual-exclusivity watcher.

Ensures only one MPRIS player is playing at a time. Watches playback state
via `playerctl --follow` and, whenever any player transitions to Playing,
pauses every other player. Symmetric: mpv is treated like any other player.

No auto-resume: players paused by this watcher stay paused until the user
resumes them manually.

Usage:
  mpris-exclusive.py   # run as a systemd user service (Type=simple)
"""

from __future__ import annotations

import subprocess
import sys

PLAYERCTL = "playerctl"


def _list_players() -> list[str]:
    """Return the names of all currently available MPRIS players."""
    result = subprocess.run(
        [PLAYERCTL, "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _pause(player: str) -> None:
    """Pause a single player. No-op if the player is already paused/stopped."""
    subprocess.run(
        [PLAYERCTL, "--player", player, "pause"],
        capture_output=True,
        text=True,
        check=False,
    )


def _pause_others(active: str) -> None:
    """Pause every player except the one that just started playing."""
    for player in _list_players():
        if player != active:
            _pause(player)


def _watch() -> int:
    """Follow MPRIS state changes and enforce exclusivity. Runs forever."""
    proc = subprocess.Popen(
        [
            PLAYERCTL,
            "--follow",
            "--all-players",
            "--format",
            "{{playerInstance}}|{{status}}",
            "status",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line or "|" not in line:
            continue
        player, status = line.split("|", 1)
        if status == "Playing":
            _pause_others(player)

    return 0


def main() -> int:
    try:
        return _watch()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
