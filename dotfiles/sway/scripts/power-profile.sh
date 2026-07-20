#!/bin/bash
# power-profile.sh — auto-switch CPU power profile based on AC/battery state

BAT_STATUS="/sys/class/power_supply/BAT0/status"
CHECK_INTERVAL=10
LAST_PROFILE=""

while true; do
    [ -f "$BAT_STATUS" ] || { sleep "$CHECK_INTERVAL"; continue; }

    status=$(cat "$BAT_STATUS")

    if [ "$status" = "Charging" ] || [ "$status" = "Full" ]; then
        profile="performance"
    else
        profile="balanced"
    fi

    if [ "$profile" != "$LAST_PROFILE" ]; then
        sudo powerprofilesctl set "$profile"
        LAST_PROFILE="$profile"
    fi

    sleep "$CHECK_INTERVAL"
done
