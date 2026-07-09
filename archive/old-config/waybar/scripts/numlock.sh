#!/bin/bash
led=$(find /sys/class/leds/ -name '*numlock' 2>/dev/null | head -1)
if [[ -n "$led" ]]; then
    val=$(cat "$led/brightness" 2>/dev/null)
    if [[ "$val" -gt 0 ]]; then
        echo '{"text": "NUM", "class": "locked"}'
    else
        echo '{"text": "num", "class": "unlocked"}'
    fi
else
    echo '{"text": "?", "class": "unlocked"}'
fi
