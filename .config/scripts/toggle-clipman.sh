#!/bin/bash
if pgrep -x wofi > /dev/null 2>&1; then
    pkill wofi
    exit 0
fi
clipman pick -t wofi 2>/dev/null || clipman pick | wofi --show dmenu
