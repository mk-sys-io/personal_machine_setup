#!/bin/bash

# bluetooth-status.sh — waybar menu bluetooth status indicator.
# Outputs JSON with text, alt, and tooltip for the custom/menu module.
# The alt field becomes a CSS class for color-based state display.

icon="󰀻"

if ! command -v bluetoothctl >/dev/null 2>&1; then
    echo "{\"text\": \"${icon}\", \"alt\": \"off\", \"tooltip\": \"Bluetooth: unavailable\"}"
    exit 0
fi

blocked=$(rfkill list bluetooth 2>/dev/null | awk '/Soft blocked:/{print $3}')
if [[ "$blocked" == "yes" ]]; then
    echo "{\"text\": \"${icon}\", \"alt\": \"off\", \"tooltip\": \"Bluetooth: off\"}"
    exit 0
fi

powered=$(bluetoothctl show 2>/dev/null | awk '/Powered:/{print $2}')
if [[ "$powered" != "yes" ]]; then
    echo "{\"text\": \"${icon}\", \"alt\": \"off\", \"tooltip\": \"Bluetooth: off\"}"
    exit 0
fi

connected=$(bluetoothctl devices Connected 2>/dev/null | head -1)
if [[ -n "$connected" ]]; then
    name=$(echo "$connected" | awk '{$1=$2; print $0}' | sed 's/^ *//')
    echo "{\"text\": \"${icon}\", \"alt\": \"connected\", \"tooltip\": \"Bluetooth: ${name}\"}"
else
    echo "{\"text\": \"${icon}\", \"alt\": \"on\", \"tooltip\": \"Bluetooth: on\"}"
fi
