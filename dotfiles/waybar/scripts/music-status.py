#!/usr/bin/env python3
"""Waybar music-status module — shows the mpv track only when it is audio.

Continuous script (no `interval` in the waybar config): it follows MPRIS
state changes via `playerctl --follow` and emits one JSON line per event, so
the bar updates instantly on play/pause/track change.

The display is filtered by file extension:
  - audio (mp3/flac/ogg/wav/m4a/opus) -> "{icon} {title} {pos}/{len}"
    (class playing/paused), with the title scrolling (marquee) when it is
    longer than MAX_TITLE_WIDTH. The timer is real-time: it re-queries the
    position every ~1s while playing.
  - video (mp4/mkv/webm/avi/mov/m4v/ts/flv) -> dimmed music icon (class video)
  - stopped / no player -> empty text (class stopped, hidden)

The built-in waybar `mpris` module cannot filter by file type, so this custom
module queries playerctl itself and decides what to show.
"""

from __future__ import annotations

import json
import select
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

PLAYERCTL = "playerctl"
PLAYER = "mpv"

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".opus"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".ts", ".flv"}

ICON_PLAYING = "\u25b6"
ICON_PAUSED = "\u23f8"
ICON_MUSIC = "\U000f0ea8"

MAX_TITLE_WIDTH = 16
SCROLL_SEPARATOR = "  \u2502  "
SCROLL_TICK = 0.2
POSITION_TICK = 1.0


def _emit(text: str, cls: str) -> None:
    """Write one JSON line for waybar and flush so it renders immediately."""
    print(json.dumps({"text": text, "class": cls}), flush=True)


def _run(args: list[str]) -> str:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _track_ext() -> str:
    """Return the lowercased file extension of the current mpv track, or ''."""
    url = _run([PLAYERCTL, "--player", PLAYER, "metadata", "--format", "{{xesam:url}}"])
    if not url:
        return ""
    path = unquote(urlparse(url).path)
    return Path(path).suffix.lower()


def _track_title() -> str:
    return _run([PLAYERCTL, "--player", PLAYER, "metadata", "--format", "{{xesam:title}}"])


def _track_position() -> float:
    """Return the current playback position in seconds, or 0.0."""
    try:
        return float(_run([PLAYERCTL, "--player", PLAYER, "position"]))
    except ValueError:
        return 0.0


def _track_length() -> float:
    """Return the track length in seconds, or 0.0."""
    raw = _run([PLAYERCTL, "--player", PLAYER, "metadata", "--format", "{{mpris:length}}"])
    try:
        return float(raw) / 1_000_000.0
    except ValueError:
        return 0.0


def _fmt_time(seconds: float) -> str:
    """Format seconds as mm:ss (or h:mm:ss when >= 1 hour)."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _scroll_window(full_text: str, max_len: int, offset: int) -> str:
    """Return a cyclic max_len-wide window over full_text + separator."""
    chars = full_text + SCROLL_SEPARATOR
    if len(chars) <= max_len:
        return full_text
    total = len(chars)
    offset %= total
    return (chars + chars)[offset : offset + max_len]


def _render(status: str, offset: int = 0) -> None:
    ext = _track_ext()
    if status in ("Playing", "Paused") and ext in AUDIO_EXTS:
        title = _track_title()
        if len(title) > MAX_TITLE_WIDTH:
            title = _scroll_window(title, MAX_TITLE_WIDTH, offset)
        pos = _fmt_time(_track_position())
        length = _fmt_time(_track_length())
        icon = ICON_PLAYING if status == "Playing" else ICON_PAUSED
        _emit(f"{icon} {title} {pos}/{length}", status.lower())
    elif status in ("Playing", "Paused") and ext in VIDEO_EXTS:
        _emit(ICON_MUSIC, "video")
    else:
        _emit("", "stopped")


def _spawn_follow() -> subprocess.Popen:
    """Start a `playerctl --follow` process for the mpv player."""
    return subprocess.Popen(
        [
            PLAYERCTL,
            "--player",
            PLAYER,
            "--follow",
            "--format",
            "{{status}}",
            "status",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )


def main() -> int:
    proc = _spawn_follow()
    stdout = proc.stdout
    assert stdout is not None
    status = ""
    offset = 0
    last_pos_query = 0.0
    last_scroll = 0.0

    while True:
        ready, _, _ = select.select([stdout], [], [], SCROLL_TICK)
        now = time.monotonic()

        if ready:
            line = stdout.readline().strip()
            if not line:
                # Empty line: the followed player disappeared (or the follow
                # process died). Emit the hidden state once and keep running
                # so we catch the next event when a new player appears.
                if status:
                    status = ""
                    _emit("", "stopped")
                if proc.poll() is not None:
                    proc = _spawn_follow()
                    stdout = proc.stdout
                    assert stdout is not None
                continue
            status = line
            offset = 0
            last_pos_query = 0.0
            last_scroll = 0.0
            _render(status, offset)
            continue

        if status == "Playing":
            if now - last_scroll >= SCROLL_TICK:
                offset += 1
                last_scroll = now
            if now - last_pos_query >= POSITION_TICK:
                last_pos_query = now
            _render(status, offset)
        elif status == "Paused":
            if now - last_pos_query >= POSITION_TICK:
                last_pos_query = now
                _render(status, offset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
