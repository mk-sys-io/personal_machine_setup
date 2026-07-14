#!/bin/bash
TERMINAL="${TERMINAL:-kitty}"

if pgrep -f "$TERMINAL.*nmtui" > /dev/null 2>&1; then
    pkill -f "$TERMINAL.*nmtui"
else
    $TERMINAL nmtui
fi
