#!/usr/bin/env python3
"""Waybar music transport button — shows its icon only while mpv is active.

Polled script (interval 1 in the waybar config). It checks whether mpv is
playing or paused and emits either the transport icon or empty text, so the
button (and the whole group) hides when playback is stopped.

Usage: music-btn.py <prev|next|close>
"""

from __future__ import annotations

import json
import subprocess
import sys

PLAYERCTL = "playerctl"
PLAYER = "mpv"

ICONS = {"prev": "\u23ee", "next": "\u23ed", "close": "\u2715"}


def _active() -> bool:
    result = subprocess.run(
        [PLAYERCTL, "--player", PLAYER, "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() in ("Playing", "Paused")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else "prev"
    icon = ICONS.get(arg, ICONS["prev"])
    text = icon if _active() else ""
    print(json.dumps({"text": text}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
