#!/bin/bash
# battery-alert.sh — polls battery every 30s, sends critical alert when < 20% and discharging

BAT_CAPACITY="/sys/class/power_supply/BAT0/capacity"
BAT_STATUS="/sys/class/power_supply/BAT0/status"
THRESHOLD=20
INTERVAL=30
NOTIFY_ID=9999

export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus"
export NLOG_TRIGGER_JSON='{"mode":"condition","invoker":"boot","unit":null}'
if command -v notify-log-send >/dev/null 2>&1; then
	notify_log_send() { notify-log-send "$@"; }
else
	notify_log_send() { /usr/bin/notify-send "$@"; }
fi

while true; do
    [ -f "$BAT_CAPACITY" ] && [ -f "$BAT_STATUS" ] || { NLOG_SUPPRESS=1 NLOG_TRIGGER_JSON='{"mode":"condition","invoker":"boot","unit":null,"decision":"suppressed","reason":"no-device"}' notify_log_send -u critical "Battery Critical" "no battery device present" >/dev/null 2>&1 || true; sleep "$INTERVAL"; continue; }

    capacity=$(cat "$BAT_CAPACITY")
    status=$(cat "$BAT_STATUS")

    if [ "$capacity" -lt "$THRESHOLD" ] && [ "$status" = "Discharging" ]; then
        powerprofilesctl set power-saver
        notify_log_send -u critical -t 7000 -r "$NOTIFY_ID" \
            "Battery Critical" \
            "${capacity}% remaining — Connect charger"
    fi

    sleep "$INTERVAL"
done
