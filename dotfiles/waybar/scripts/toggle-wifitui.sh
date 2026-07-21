#!/bin/bash
TERMINAL="${TERMINAL:-kitty}"

if pgrep -f "$TERMINAL.*wifitui" > /dev/null 2>&1; then
    pkill -f "$TERMINAL.*wifitui"
else
    $TERMINAL wifitui
fi
