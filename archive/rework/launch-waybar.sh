#!/bin/bash
LOG=/tmp/waybar-launch.log

echo "===== $(date) =====" >> "$LOG"
echo "started by: $(ps -o comm= $PPID)" >> "$LOG"
echo "XDG_RUNTIME_DIR: $XDG_RUNTIME_DIR" >> "$LOG"

SOCKET="${XDG_RUNTIME_DIR}/wayland-1"
echo "waiting for $SOCKET..." >> "$LOG"

wait=0
until [ -e "$SOCKET" ]; do
    sleep 1
    wait=$((wait + 1))
done
echo "socket ready after ${wait}s" >> "$LOG"

while true; do
    waybar -c ~/.config/waybar/config.json -s ~/.config/waybar/style.css >> "$LOG" 2>/dev/null
    echo "waybar exited (code $?), restarting..." >> "$LOG"
    sleep 1
done
