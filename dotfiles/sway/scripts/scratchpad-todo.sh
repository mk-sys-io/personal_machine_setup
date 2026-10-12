#!/bin/bash
# Toggle scratchpad todo panel (kitty --class scratchpad-todo + nano).
# Espanso-style kill-toggle: every open is a fresh window, so the
# current for_window rule always applies (no stale geometry).
# Self-healing: ensures the todo file exists on every invocation,
# so a fresh machine or a manually deleted file recreates cleanly.
# NOTE: toggle-off kills the editor — save (^O) first, unsaved text is lost.
set -eu -o pipefail

APP="scratchpad-todo"
FILE="$HOME/.local/share/scratchpad/todo.md"

mkdir -p "$(dirname "$FILE")"
if [ ! -s "$FILE" ]; then
    printf '# Scratchpad\n' > "$FILE"
fi

if swaymsg -t get_tree | jq -e '.. | objects | select(.app_id == "'"$APP"'")' >/dev/null 2>&1; then
    swaymsg '[app_id="'"$APP"'"] kill'
else
    kitty --class "$APP" -e nano "$FILE" &
fi
