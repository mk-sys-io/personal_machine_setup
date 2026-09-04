#!/usr/bin/env python3
"""Rofi-based music picker for mpv.

Scans ~/Music, shows a rofi dropdown, and launches the selection in mpv
--no-video in the background. The waybar mpris module picks up playback.

Only music stored at the root level of ~/Music is supported — subfolders are
not supported in any way (they are neither shown nor played). The picker
launches mpv with a playlist of every root-level audio file, starting at the
selected song, so the waybar prev/next buttons can navigate the library.

Error handling: all errors display inside the rofi panel (not notifications).
The user sees the error in context, presses Escape to dismiss, and re-clicks
the launcher icon to try again.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROFI_CONFIG = Path.home() / ".config/sway/rofi/music-picker.rasi"
MUSIC_DIR = Path.home() / "Music"

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".opus"}


def _show_error(message: str) -> None:
    """Show a message inside the rofi panel."""
    subprocess.run(
        ["rofi", "-dmenu", "-i", "-p", "Error", "-config", str(ROFI_CONFIG)],
        input=message,
        text=True,
        check=False,
    )


def _check_deps() -> bool:
    missing = [cmd for cmd in ("mpv", "playerctl") if shutil.which(cmd) is None]
    if missing:
        _show_error("\n".join(f"Missing: {cmd}" for cmd in missing))
        return False
    return True


def _scan() -> list[Path]:
    """Return playable audio files at the root of ~/Music, sorted by name.

    Only files directly in ~/Music are supported — subfolders are ignored
    entirely (not shown, not played).
    """
    if not MUSIC_DIR.is_dir():
        return []
    return sorted(
        (
            entry
            for entry in MUSIC_DIR.iterdir()
            if entry.is_file()
            and entry.suffix.lower() in AUDIO_EXTS
            and entry.stat().st_size > 0
            and os.access(entry, os.R_OK)
        ),
        key=lambda p: p.name,
    )


def _pick(entries: list[str]) -> str:
    result = subprocess.run(
        ["rofi", "-dmenu", "-i", "-p", "Music", "-config", str(ROFI_CONFIG)],
        input="\n".join(entries),
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.rstrip("\n")


def main() -> int:
    if not _check_deps():
        return 1

    if not MUSIC_DIR.is_dir():
        _show_error("Music folder does not exist")
        return 1

    files = _scan()
    if not files:
        _show_error("No music files in ~/Music")
        return 1

    entries = [f"\U0001f3b5 {p.name}" for p in files]
    chosen = _pick(entries)
    if not chosen:
        return 0

    selection = chosen.split(" ", 1)[1] if " " in chosen else chosen
    target = next((p for p in files if p.name == selection), None)
    if target is None:
        _show_error(f"File not found: {selection}")
        return 1

    start = files.index(target)
    subprocess.Popen(
        [
            "mpv",
            "--no-video",
            "--loop-playlist=inf",
            f"--playlist-start={start}",
            *[str(p) for p in files],
        ]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
