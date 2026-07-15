#!/bin/bash

# bluetooth.sh — waybar Bluetooth indicator.
# Outputs JSON with text and class for waybar's custom module.
# States:
#   off     — radio blocked or adapter not found
#   on      — adapter powered on, no device connected
#   connected — device connected (shows device name)

if ! command -v bluetoothctl >/dev/null 2>&1; then
    echo '{"text": "󰂯 Off", "class": "off"}'
    exit 0
fi

# Check if radio is blocked
blocked=$(rfkill list bluetooth 2>/dev/null | awk '/Soft blocked:/{print $3}')
if [[ "$blocked" == "yes" ]]; then
    echo '{"text": "󰂯 Off", "class": "off"}'
    exit 0
fi

# Check if adapter is powered
powered=$(bluetoothctl show 2>/dev/null | awk '/Powered:/{print $2}')
if [[ "$powered" != "yes" ]]; then
    echo '{"text": "󰂯 Off", "class": "off"}'
    exit 0
fi

# Check for connected devices
connected=$(bluetoothctl devices Connected 2>/dev/null | head -1)
if [[ -n "$connected" ]]; then
    # Extract device name (everything after the second space)
    name=$(echo "$connected" | awk '{$1=$2; print $0}' | sed 's/^ *//')
    if [[ -n "$name" ]]; then
        echo "{\"text\": \"󰂯 ${name}\", \"class\": \"connected\"}"
    else
        echo '{"text": "󰂯 Connected", "class": "connected"}'
    fi
    exit 0
fi

# Powered on but no device connected
echo '{"text": "󰂯 On", "class": "on"}'
