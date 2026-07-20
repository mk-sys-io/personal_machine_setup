#!/bin/bash
# battery-alert.sh — polls battery every 30s, sends critical alert when < 20% and discharging

BAT_CAPACITY="/sys/class/power_supply/BAT0/capacity"
BAT_STATUS="/sys/class/power_supply/BAT0/status"
THRESHOLD=20
INTERVAL=30
NOTIFY_ID=9999

export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus"

while true; do
    [ -f "$BAT_CAPACITY" ] && [ -f "$BAT_STATUS" ] || { sleep "$INTERVAL"; continue; }

    capacity=$(cat "$BAT_CAPACITY")
    status=$(cat "$BAT_STATUS")

    if [ "$capacity" -lt "$THRESHOLD" ] && [ "$status" = "Discharging" ]; then
        powerprofilesctl set power-saver
        notify-send -u critical -t 7000 -r "$NOTIFY_ID" \
            "Battery Critical" \
            "${capacity}% remaining — Connect charger"
    fi

    sleep "$INTERVAL"
done
