#!/bin/bash

# system-menu.sh — Rofi system menu for waybar.
# Shows Screenshot, Power, and other entries.
# Launched via on-click on the waybar custom/menu module.

ROFI_THEME="$HOME/.config/sway/rofi/config.rasi"

# --- Power profile status ---
pp_icon="󰓅"
pp_profile="balanced"

if command -v powerprofilesctl >/dev/null 2>&1; then
    pp_profile=$(powerprofilesctl get 2>/dev/null || echo "balanced")
fi

case "$pp_profile" in
    performance) pp_icon="󰓅" ;;
    balanced)    pp_icon="󰾅" ;;
    power-saver) pp_icon="󰾆" ;;
esac

# --- Night light status ---
nl_icon="󰃟"

if pgrep -x wlsunset > /dev/null 2>&1; then
    nl_status="Enabled"
else
    nl_status="Disabled"
fi

# --- Current theme status ---
theme_icon="󰏒"
theme_name="Unknown"
current_link="$HOME/.config/sway/current-theme"
if [ -L "$current_link" ]; then
    current_dir=$(basename "$(readlink "$current_link")")
    theme_conf="$HOME/.config/sway/themes/$current_dir/theme.conf"
    if [ -f "$theme_conf" ]; then
        theme_name=$(awk -F'=' '/^name\s*=/{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}' "$theme_conf")
        [ -z "$theme_name" ] && theme_name="$current_dir"
    fi
fi

# --- Build menu ---
entries=()
entries+=("${theme_icon} Theme: ${theme_name}")
entries+=("${pp_icon} Power Profile: ${pp_profile}")
entries+=("${nl_icon} Night Light: ${nl_status}")
entries+=("󰹑 Screenshot")
entries+=("⏻ Power")

chosen=$(printf '%s\n' "${entries[@]}" | \
    rofi -dmenu -i -p "System" -theme "$ROFI_THEME")

[ -z "$chosen" ] && exit 0

# --- Handle selection ---
case "$chosen" in
    *"Theme"*)
        ~/.config/sway/scripts/thememenu &
        ;;
    *"Power Profile"*)
        subchosen=$(printf '%s\n' "  performance" "  balanced" "  power-saver" | \
            rofi -dmenu -i -p "Power Profile" -theme "$ROFI_THEME")
        [ -z "$subchosen" ] && exit 0
        case "$subchosen" in
            *"performance") sudo powerprofilesctl set performance ;;
            *"balanced")    sudo powerprofilesctl set balanced ;;
            *"power-saver") sudo powerprofilesctl set power-saver ;;
        esac
        ;;
    *"Night Light"*)
        pkill -USR2 -f wlsunset-notify.py 2>/dev/null || \
            ~/.config/sway/scripts/wlsunset-notify.py &
        ;;
    *"Screenshot"*)
        subchosen=$(printf '%s\n' "Region" "Fullscreen" "Clipboard" | \
            rofi -dmenu -i -p "Screenshot" -theme "$ROFI_THEME")
        [ -z "$subchosen" ] && exit 0
        case "$subchosen" in
            "Region") ~/.config/sway/scripts/screenshot region ;;
            "Fullscreen") ~/.config/sway/scripts/screenshot full ;;
            "Clipboard") ~/.config/sway/scripts/screenshot copy ;;
        esac
        ;;
    *"Power"*)
        ~/.config/sway/scripts/power
        ;;
esac
