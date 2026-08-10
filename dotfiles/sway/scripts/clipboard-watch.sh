#!/bin/bash
# clipboard-watch.sh — persist clipboard to cliphist + show transient "Copied" toast.
#   stdin = clipboard payload (passed untouched to cliphist store, binary-safe).
#   CLIPBOARD_STATE is set by wl-paste --watch: data|nil|clear|sensitive.
#
# Spurious-notification guards:
#   1. Hash dedup — same clipboard content → skip (apps re-asserting the selection
#      on focus produce identical payloads; not real copies).
#   2. Throttle — <500ms since last toast → skip (rapid-fire copies stack toasts).
#   3. close-latest — replace any lingering toast instead of stacking.
case "$CLIPBOARD_STATE" in
  data | sensitive) ;;
  *) exit 0 ;;   # nil/clear — clipboard emptied (wipe), not a copy
esac

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/waybar-weather"
HASH_FILE="$CACHE_DIR/clipboard-hash"
TS_FILE="$CACHE_DIR/clipboard-ts"
THROTTLE_MS=500

mkdir -p "$CACHE_DIR"

# Guard 1: hash dedup
CONTENT=$(cat)
NEW_HASH=$(printf '%s' "$CONTENT" | sha256sum | cut -d' ' -f1)
OLD_HASH=$(cat "$HASH_FILE" 2>/dev/null)
[ "$NEW_HASH" = "$OLD_HASH" ] && exit 0

# Guard 2: throttle
NOW_MS=$(($(date +%s%N) / 1000000))
LAST_MS=$(cat "$TS_FILE" 2>/dev/null || echo 0)
[ $((NOW_MS - LAST_MS)) -lt "$THROTTLE_MS" ] && exit 0

# Guard 3: replace previous toast, then store + notify
swaync-client --close-latest 2>/dev/null
printf '%s' "$CONTENT" | cliphist -max-items 250 store
printf '%s' "$NEW_HASH" > "$HASH_FILE"
printf '%s' "$NOW_MS" > "$TS_FILE"
notify-send -r 1001 -e -t 1500 -a "" -u low "Copied"
