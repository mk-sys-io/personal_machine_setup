#!/bin/bash

STATE_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/bt-idle-epoch"
IDLE_TIMEOUT=180

if ! bluetoothctl show | grep -q 'Powered: yes'; then
    rm -f "$STATE_FILE"
    exit 0
fi

CONNECTED=$(bluetoothctl devices Connected | grep -c '^Device')
if [ "$CONNECTED" -gt 0 ]; then
    rm -f "$STATE_FILE"
    exit 0
fi

NOW=$(date +%s)

if [ -f "$STATE_FILE" ]; then
    IDLE_SINCE=$(cat "$STATE_FILE")
    ELAPSED=$((NOW - IDLE_SINCE))
    if [ "$ELAPSED" -ge "$IDLE_TIMEOUT" ]; then
        bluetoothctl power off
        notify-send -a "bluetooth-idle" -e -u low -t 3000 "Bluetooth" "Turned off after 3 min idle"
        rm -f "$STATE_FILE"
    fi
else
    echo "$NOW" > "$STATE_FILE"
fi
