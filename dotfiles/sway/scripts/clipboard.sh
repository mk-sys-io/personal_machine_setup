#!/bin/bash
# clipboard.sh — cliphist picker.
#   Enter = copy · 🗑️ Clear all history = wipe (confirm)
ROFI_THEME="$HOME/.config/sway/rofi/config.rasi"

confirm_wipe() {
  choice="$(printf 'No\nYes\n' | rofi -dmenu -config "$ROFI_THEME" -display-columns 1 -p "Clear all history?")"
  [ "$choice" = "Yes" ] && cliphist wipe && wl-copy --clear
}

out="$( {
  printf '🗑️  Clear all history\tCLEAR\n'
  cliphist list | sed -E 's/^([0-9]+)\t(.*)$/📋 \2\t\1/'
} | rofi -dmenu -config "$ROFI_THEME" -display-columns 1 -p "Clipboard" -mesg "Enter: copy  ·  🗑️: wipe all" )"

case "$out" in
  "" ) exit 0 ;;
  *$'\tCLEAR' ) confirm_wipe; exit 0 ;;
esac

id="$(printf '%s\n' "$out" | sed -E 's/^.*\t([0-9]+)$/\1/')"
case "$id" in *[!0-9]*|"") exit 0 ;; esac
printf '%s' "$id" | cliphist decode | wl-copy
