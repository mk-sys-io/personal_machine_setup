#!/usr/bin/env python3
"""clipboard-toast.py - "Copied" toast driven off clipse's own history.

Single-owner design (clipse owns the clipboard; we only react to its history):
  - Long-lived exes are python3 + inotifywait - invisible to
    clipse -listen's KillExisting (which only targets exes that are
    substrings of "wl-paste" or named "clipse"), so this watcher survives
    boot and sway reload - unlike the old wl-paste --watch toast watcher
    that clipse SIGTERM'd and never respawned.
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
import subprocess
import sys
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

NOTIFY_ARGS = [
    "notify-send",
    "-r", "1001",
    "-e",
    "-t", "1500",
    "-a", "",
    "-u", "low",
    "Copied",
]


def ident(entry: dict[str, Any]) -> str:
    """Content identity hash for one history entry."""
    payload = json.dumps(
        {"value": entry.get("value", ""), "filePath": entry.get("filePath", "")},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def is_image(entry: dict[str, Any]) -> bool:
    """True if the entry is an image (real filePath, not null)."""
    fp = entry.get("filePath")
    return bool(fp) and fp not in ("null", "")


def read_history() -> list[dict[str, Any]]:
    """Parse the history file. Missing/corrupt content yields [] (never raises)."""
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            data = json.load(f)
        history = data.get("clipboardHistory") or []
        return [e for e in history if isinstance(e, dict)]
    except (OSError, ValueError, AttributeError):
        return []


def newest(history: list[dict[str, Any]], *, images: bool) -> str:
    """Ident of the newest text (or image) entry, or empty when there is none."""
    candidates = [e for e in history if is_image(e) is images]
    if not candidates:
        return ""
    try:
        top = max(candidates, key=lambda e: str(e.get("recorded", "")))
    except Exception:
        top = candidates[0]
    return ident(top)


def all_idents(history: list[dict[str, Any]]) -> set[str]:
    """Every content ident currently in history (deduped)."""
    return {ident(e) for e in history}


def load_seen() -> set[str]:
    """Read the persisted seen-set. Missing/unreadable file yields empty set."""
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return set()


def persist_seen(seen: set[str]) -> None:
    """Persist the seen-set atomically (write-temp + rename)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for hsh in sorted(seen):
            f.write(hsh + "\n")
    os.replace(tmp, SEEN_FILE)


def drop_legacy_hash_file() -> None:
    """Remove the vestigial toast-hash file from the bash predecessor."""
    try:
        os.remove(LEGACY_HASH_FILE)
    except OSError:
        pass


def notify_copied() -> None:
    """Emit the Copied bubble (same args as the bash predecessor)."""
    subprocess.run(NOTIFY_ARGS, check=False)


def process_event(seen: set[str]) -> tuple[bool, set[str]]:
    """Handle one history-file write. Returns (toasted, updated_seen).

    Toasts only when the current newest text/image ident was never seen
    before. Always refreshes the baseline to the current set so deleted
    entries are forgotten (delete-then-recopy correctly toasts as new).
    """
    history = read_history()
    new_text = newest(history, images=False)
    new_image = newest(history, images=True)
    if not new_text and not new_image:
        # History wiped: reset the baseline, not a copy.
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
    """Run the watcher forever. Returns 0 when inotifywait goes away."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    drop_legacy_hash_file()
    if os.path.isfile(SEEN_FILE):
        seen = load_seen()
    else:
        # First run / upgrade: seed from live history, no toast for
        # pre-existing entries.
        seen = all_idents(read_history())
        persist_seen(seen)
    while True:
        try:
            result = subprocess.run(
                [
                    "inotifywait",
                    "-q",
                    "-e", "close_write",
                    "-e", "moved_to",
                    "--format", "%f",
                    HIST_DIR,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            break
        if result.returncode != 0:
            break
        names = {
            line.strip() for line in result.stdout.splitlines() if line.strip()
        }
        if HIST_NAME not in names:
            continue
        _, seen = process_event(seen)
    return 0


def main() -> int:
    """Entry point."""
    return watch()


if __name__ == "__main__":
    sys.exit(main())
