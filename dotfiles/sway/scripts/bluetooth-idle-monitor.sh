#!/bin/bash

STATE_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/bt-idle-epoch"
IDLE_TIMEOUT=180

export NLOG_TRIGGER_JSON='{"mode":"condition","invoker":"systemd-timer","unit":"bluetooth-idle-monitor.timer"}'
# Resolve the logging helper by absolute path: this unit runs under the systemd
# user manager whose PATH does not include ~/.local/bin, so `command -v` would
# always miss it and silently fall back to a real notify-send (see the
# "no radio present" popup bug). Fall back only when the helper is genuinely
# absent (e.g. before `make tools`).
NLS="$HOME/.local/bin/notify-log-send"
if [ -x "$NLS" ]; then
	notify_log_send() { "$NLS" "$@"; }
else
	notify_log_send() { /usr/bin/notify-send "$@"; }
fi

if ! bluetoothctl show | grep -q 'Powered: yes'; then
    NLOG_SUPPRESS=1 NLOG_TRIGGER_JSON='{"mode":"condition","invoker":"systemd-timer","unit":"bluetooth-idle-monitor.timer","decision":"suppressed","reason":"no-device"}' notify_log_send -a "bluetooth-idle" -e -u low -t 3000 "Bluetooth" "no radio present" >/dev/null 2>&1 || true
    rm -f "$STATE_FILE"
    exit 0
fi

CONNECTED=$(bluetoothctl devices Connected | grep -c '^Device')
if [ "$CONNECTED" -gt 0 ]; then
    NLOG_SUPPRESS=1 NLOG_TRIGGER_JSON='{"mode":"condition","invoker":"systemd-timer","unit":"bluetooth-idle-monitor.timer","decision":"suppressed","reason":"no-device"}' notify_log_send -a "bluetooth-idle" -e -u low -t 3000 "Bluetooth" "device connected" >/dev/null 2>&1 || true
    rm -f "$STATE_FILE"
    exit 0
fi

NOW=$(date +%s)

if [ -f "$STATE_FILE" ]; then
    IDLE_SINCE=$(cat "$STATE_FILE")
    ELAPSED=$((NOW - IDLE_SINCE))
    if [ "$ELAPSED" -ge "$IDLE_TIMEOUT" ]; then
        bluetoothctl power off
        NLOG_TRIGGER_JSON="{\"mode\":\"condition\",\"invoker\":\"systemd-timer\",\"unit\":\"bluetooth-idle-monitor.timer\",\"expected_at\":$((IDLE_SINCE + IDLE_TIMEOUT))}" notify_log_send -a "bluetooth-idle" -e -u low -t 3000 "Bluetooth" "Turned off after 3 min idle"
        rm -f "$STATE_FILE"
    fi
else
    echo "$NOW" > "$STATE_FILE"
fi
