#!/bin/bash
## clipboard-toast.sh — "Copied" toast driven off clipse's own history.
## Single-owner design (clipse owns the clipboard; we only react to its history):
##   - exe = inotifywait  → invisible to clipse -listen's KillExisting() (which only
##     targets exes that are substrings of "wl-paste" or named "clipse"), so this
##     watcher survives boot, sway reload, and swaymsg reload — unlike our old
##     wl-paste --watch toast watcher that clipse SIGTERM'd + never respawned.
##   - clipse allowDuplicates:false → a sway reload re-delivers the SAME selection,
##     which appends NO new history entry → file unchanged → no inotify event →
##     no fake toast. Real new copies append an entry → close_write fires → toast.
##   - autostart guards on the detached listeners (not `clipse -listen`, which
##     exits) → reloads no longer respawn listeners → no refire at all.
##   - Watches the history DIRECTORY (not the file inode): survives atomic
##     rewrites of the history file (write-temp + rename) that would silently
##     kill a file-inode watch. Dir events are filtered to the history filename;
##     the content-hash dedup below skips everything else.
## Guarded launch (single instance) from autostart.sh. Not a wl-paste/clipse exe.
set -euo pipefail

HIST_FILE="$HOME/.config/clipse/clipboard_history.json"
HIST_DIR="$(dirname "$HIST_FILE")"
HIST_NAME="$(basename "$HIST_FILE")"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/sway-clipboard"
HASH_FILE="$CACHE_DIR/toast-hash"   # two lines: <text-hash>\n<image-hash>

mkdir -p "$CACHE_DIR"

## current_hashes — print newest TEXT hash + newest IMAGE hash (one per line).
## Split tracking: with an image selected clipse stores both an image entry
## (real filePath) and a text entry (filePath "null"), and newest-by-recorded
## can flip between them on re-delivery → a single hash would toast with zero
## user interaction. Separate hashes stay stable. Hashes CONTENT IDENTITY ONLY
## (value + filePath), never the whole entry (recorded churn must not toast).
current_hashes() {
  python3 - "$HIST_FILE" <<'PY'
import json, sys, hashlib
def ident(e):
    return hashlib.sha256(json.dumps({"value": e.get("value",""), "filePath": e.get("filePath","")}, sort_keys=True).encode()).hexdigest()
def is_image(e):
    fp = e.get("filePath")
    return bool(fp) and fp not in ("null", "")
def newest(es):
    if not es:
        return ""
    try:
        top = max(es, key=lambda e: e.get("recorded",""))
    except Exception:
        top = es[0]
    return ident(top)
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    h = data.get("clipboardHistory") or []
    print(newest([e for e in h if not is_image(e)]))
    print(newest([e for e in h if is_image(e)]))
except Exception:
    print("")
    print("")
PY
}

## read_current — set $new_text / $new_image from live history.
read_current() {
  local lines
  mapfile -t lines < <(current_hashes)
  new_text="${lines[0]:-}"
  new_image="${lines[1]:-}"
}

persist_hashes() {
  printf '%s\n%s\n' "$last_text" "$last_image" > "$HASH_FILE"
}

notify_copied() {
  notify-send -r 1001 -e -t 1500 -a "" -u low "Copied"
  last_text="$new_text"; last_image="$new_image"
  persist_hashes
}

## Baseline: prefer persisted hashes (continuity across restarts — a copy made
## while the watcher was dead still toasts on the next event); fall back to
## live history (also migrates the old single-hash file without a spurious
## toast — a fresh watcher only announces genuinely new arrivals).
last_text=""; last_image=""
if [[ -f "$HASH_FILE" ]]; then
  mapfile -t saved < "$HASH_FILE" || true
  if [[ "${#saved[@]}" -eq 2 ]]; then
    last_text="${saved[0]}"; last_image="${saved[1]}"
  fi
fi
if [[ -z "$last_text" && -z "$last_image" ]]; then
  read_current
  last_text="$new_text"; last_image="$new_image"
  persist_hashes
fi

## Watch the directory for writes to clipse's history file, forever.
## Directory (not file-inode) watch: survives atomic rewrites of the history
## file (write-temp + rename) that would silently kill a file-inode watch.
## The dir watch fires for every file (clipse.log, theme, …) — proceed only
## when the history file itself was written. inotifywait failure (missing
## binary, dir gone) breaks the loop instead of busy-spinning.
while true; do
  if ! out="$(inotifywait -q -e close_write -e moved_to --format '%f' "$HIST_DIR")"; then
    break
  fi
  printf '%s\n' "$out" | grep -qxF "$HIST_NAME" || continue
  read_current
  [ -n "$new_text$new_image" ] || continue   # both empty = history wiped, not a copy
  if [ "$new_text" != "$last_text" ] || [ "$new_image" != "$last_image" ]; then
    notify_copied
  fi
done
