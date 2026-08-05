#!/bin/bash

# idle-guard.sh — skip swayidle actions while audio is actively playing.
# Any app (VLC, Brave, mpv, game) with a RUNNING output stream keeps the
# screen awake, regardless of which window is focused or visible.
# Usage: idle-guard.sh <dim|lock|dpms-off|suspend>

# pactl needs the session runtime dir; guard against a stripped-down env
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

active_audio() {
	# A sink-input reports Corked: no while playing, Corked: yes while paused.
	# (pactl puts "State:" on sinks, not sink-inputs.)
	pactl list sink-inputs 2>/dev/null | grep -q "Corked: no"
}

case "$1" in
	dim)
		active_audio && exit 0
		exec "$HOME/.config/sway/scripts/brightness-dim.sh" dim
		;;
	lock)
		active_audio && exit 0
		exec gtklock
		;;
	dpms-off)
		active_audio && exit 0
		exec swaymsg "output * dpms off"
		;;
	suspend)
		active_audio && exit 0
		exec systemctl suspend
		;;
esac
