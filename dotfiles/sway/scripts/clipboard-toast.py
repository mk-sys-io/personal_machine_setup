#!/usr/bin/env python3
"""clipboard-toast.py - "Copied" toast driven off clipse's own history.

Single-owner design (clipse owns the clipboard; we only react to its history):
  - Long-lived exes are python3 + one persistent inotifywait monitor child,
    both invisible to clipse -listen's KillExisting (which only targets exes
    whose names contain wl-paste or equal clipse), so this watcher survives
    boot and sway reload - unlike the old wl-paste watch toast watcher
    that clipse SIGTERM'd and never respawned.
  - Persistent monitor: a single inotifywait -m child streams history events
    for the life of the watcher (no per-event respawn gap), with a
    kernel-side include filter so clipse.log / theme / tmp writes never
    steal the wakeup. Rapid text+image double-writes are drained into one
    burst and handled by a single process_event call.
  - Toast predicate is "newest content NEVER SEEN before" (tracked as a set
    of known content idents in toast-seen), not "newest changed". A
    delete/pin/reorder/clear only removes or reshuffles already-known
    entries, so it can never introduce an unseen ident and never toasts.
    Only a genuinely new clipboard arrival toasts - including at the
    maxHistory cap, where a copy evicts the oldest instead of growing the
    list (a length check would miss it). Re-copying already-present content
    stays silent, matching the reload behavior.
  - Watches the history DIRECTORY (not the file inode): survives atomic
    rewrites of the history file (write-temp + rename) that would silently
    kill a file-inode watch. Dir events are filtered to the history
    filename; everything else (clipse.log, theme files) is skipped.
  - Content identity is value + filePath only - never the whole entry,
    so recorded churn never toasts. Text and image newest are tracked
    separately (with an image selected, clipse stores both an image entry
    and a text entry, and newest-by-recorded can flip between them on
    re-delivery).

State: CACHE/sway-clipboard/toast-seen (one sha per line, <= maxHistory).
The legacy toast-hash file from the bash predecessor is removed on
startup - the persisted seen-set already subsumes its "copy made while the
watcher was dead" continuity, so nothing reads it anymore.

Usage:
  clipboard-toast.py   # guarded single instance from autostart.sh
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import time
from typing import Any

HIST_FILE = os.path.expanduser("~/.config/clipse/clipboard_history.json")
HIST_DIR = os.path.dirname(HIST_FILE)
HIST_NAME = os.path.basename(HIST_FILE)
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "sway-clipboard",
)
SEEN_FILE = os.path.join(CACHE_DIR, "toast-seen")
LEGACY_HASH_FILE = os.path.join(CACHE_DIR, "toast-hash")

INCLUDE_PATTERN = r"clipboard_history\.json$"
BURST_DRAIN_SECONDS = 0.08

NOTIFY_ARGS = [
    "notify-send",
    "-r", "1001",
    "-e",
    "-t", "1500",
    "-a", "",
    "-u", "low",
    "Copied",
]

_child: subprocess.Popen[str] | None = None


def _terminate_child() -> None:
    global _child
    proc, _child = _child, None
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def _handle_term(signum: int, _frame: object) -> None:
    _terminate_child()
    sys.exit(128 + signum)


def ident(entry: dict[str, Any]) -> str:
    payload = json.dumps(
        {"value": entry.get("value", ""), "filePath": entry.get("filePath", "")},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def is_image(entry: dict[str, Any]) -> bool:
    fp = entry.get("filePath")
    return bool(fp) and fp not in ("null", "")


def read_history() -> list[dict[str, Any]]:
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            data = json.load(f)
        history = data.get("clipboardHistory") or []
        return [e for e in history if isinstance(e, dict)]
    except (OSError, ValueError, AttributeError):
        return []


def newest(history: list[dict[str, Any]], *, images: bool) -> str:
    candidates = [e for e in history if is_image(e) is images]
    if not candidates:
        return ""
    try:
        top = max(candidates, key=lambda e: str(e.get("recorded", "")))
    except Exception:
        top = candidates[0]
    return ident(top)


def all_idents(history: list[dict[str, Any]]) -> set[str]:
    return {ident(e) for e in history}


def load_seen() -> set[str]:
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return set()


def persist_seen(seen: set[str]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for hsh in sorted(seen):
            f.write(hsh + "\n")
    os.replace(tmp, SEEN_FILE)


def drop_legacy_hash_file() -> None:
    try:
        os.remove(LEGACY_HASH_FILE)
    except OSError:
        pass


def notify_copied() -> None:
    subprocess.run(NOTIFY_ARGS, check=False)


def process_event(seen: set[str]) -> tuple[bool, set[str]]:
    history = read_history()
    new_text = newest(history, images=False)
    new_image = newest(history, images=True)
    if not new_text and not new_image:
        persist_seen(set())
        return False, set()
    toasted = bool(
        (new_text and new_text not in seen)
        or (new_image and new_image not in seen)
    )
    if toasted:
        notify_copied()
    current = all_idents(history)
    persist_seen(current)
    return toasted, current


def watch() -> int:
    global _child
    os.makedirs(CACHE_DIR, exist_ok=True)
    drop_legacy_hash_file()
    if os.path.isfile(SEEN_FILE):
        seen = load_seen()
    else:
        seen = all_idents(read_history())
        persist_seen(seen)
    try:
        proc = subprocess.Popen(
            [
                "inotifywait",
                "-m",
                "-q",
                "-e", "close_write",
                "-e", "moved_to",
                "--format", "%f",
                "--include", INCLUDE_PATTERN,
                HIST_DIR,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError:
        return 0
    if proc.stdout is None:
        return 0
    _child = proc
    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)
    try:
        while True:
            line = proc.stdout.readline()
            if line == "":
                break
            if line.strip() != HIST_NAME:
                continue
            eof = False
            deadline = time.monotonic() + BURST_DRAIN_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select([proc.stdout], [], [], remaining)
                if not ready:
                    break
                extra = proc.stdout.readline()
                if extra == "":
                    eof = True
                    break
            if eof:
                _, seen = process_event(seen)
                break
            _, seen = process_event(seen)
    finally:
        _terminate_child()
    return 0


def main() -> int:
    return watch()


if __name__ == "__main__":
    sys.exit(main())
