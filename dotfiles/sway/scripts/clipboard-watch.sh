#!/bin/bash
# clipboard-watch.sh — persist clipboard to cliphist + show transient "Copied" toast.
#   stdin = clipboard payload (passed untouched to cliphist store, binary-safe).
#   CLIPBOARD_STATE is set by wl-paste --watch: data|nil|clear|sensitive.
case "$CLIPBOARD_STATE" in
  data | sensitive) ;;
  *) exit 0 ;;   # nil/clear — clipboard emptied (wipe), not a copy
esac

cliphist -max-items 250 store
notify-send -t 1500 -e -r 1001 -a "" -u low "Copied"
