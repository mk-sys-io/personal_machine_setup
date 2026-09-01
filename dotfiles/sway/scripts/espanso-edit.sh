#!/bin/bash
# Toggle espanso match editor (kitty --class floating-editor)
if swaymsg -t get_tree | jq -e '.. | objects | select(.app_id == "floating-editor")' >/dev/null 2>&1; then
    swaymsg '[app_id="floating-editor"] kill'
else
    kitty --class floating-editor -e nano ~/.config/espanso/match/base.yml &
fi