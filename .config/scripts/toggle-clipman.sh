#!/bin/bash
if pgrep -x fuzzel > /dev/null 2>&1; then
    pkill fuzzel
    exit 0
fi
clipman pick | fuzzel --dmenu
