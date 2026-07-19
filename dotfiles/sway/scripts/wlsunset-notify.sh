#!/bin/bash
# wlsunset-notify — sends notifications at blue-light filter transitions
# Works alongside wlsunset which handles the actual gamma changes

notify_off="Blue light filter deactivated"
notify_on="Blue light filter activated"

seconds_until() {
    local target_hour=$1
    local now_seconds
    now_seconds=$(date -u -d "today $HOUR:$MINUTE" +%s 2>/dev/null || date +%s)
    local target_seconds
    target_seconds=$(date -u -d "today $target_hour:00" +%s 2>/dev/null)
    if [ "$target_seconds" -le "$now_seconds" ]; then
        target_seconds=$(date -u -d "tomorrow $target_hour:00" +%s 2>/dev/null)
    fi
    echo $(( target_seconds - now_seconds ))
}

# Wait for swaync to restart after autostart.sh kills it
sleep 2

while true; do
    HOUR=$(date +%H)
    MINUTE=$(date +%M)

    if [ "$HOUR" -ge 17 ] || [ "$HOUR" -lt 6 ]; then
        # Currently in night mode — notify immediately, then wait until 06:00
        notify-send -u low -i display-brightness-low "$notify_on"
        sleep_sec=$(seconds_until 6)
        sleep "$sleep_sec"
        notify-send -u low -i display-brightness-low "$notify_off"
    else
        # Currently in day mode — notify immediately, then wait until 17:00
        notify-send -u low -i display-brightness-low "$notify_off"
        sleep_sec=$(seconds_until 17)
        sleep "$sleep_sec"
        notify-send -u low -i display-brightness-low "$notify_on"
    fi
done
