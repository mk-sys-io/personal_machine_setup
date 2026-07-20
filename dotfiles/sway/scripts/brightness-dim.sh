#!/bin/bash

# brightness-dim.sh — relative screen dim for swayidle
#
# Usage:
#   brightness-dim.sh dim      save brightness, dim to 1/3 (skip if ≤ 5%)
#   brightness-dim.sh restore  restore saved brightness

STATE_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/sway/brightness"

case "$1" in
    dim)
        current=$(brightnessctl get)
        max=$(brightnessctl max)
        pct=$(( current * 100 / max ))

        if [ "$pct" -gt 5 ]; then
            mkdir -p "$(dirname "$STATE_FILE")"
            echo "$current" > "$STATE_FILE"
            brightnessctl set $(( pct / 3 ))%
        fi
        ;;
    restore)
        if [ -f "$STATE_FILE" ]; then
            saved=$(cat "$STATE_FILE")
            brightnessctl set "$saved"
            rm -f "$STATE_FILE"
        fi
        ;;
esac
