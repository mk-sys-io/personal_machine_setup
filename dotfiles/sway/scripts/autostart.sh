#!/bin/bash

# Kill orphaned instances from prior sway reloads
for pid in $(pgrep -f "sway/scripts/autostart.sh"); do
    [ "$pid" != "$$" ] && kill "$pid" 2>/dev/null
done
pkill -x waybar
pkill -f "waybar/scripts/music-status.py"
pkill -x swaync
pkill -x swaync-client
pkill -x sys-alert
pkill -x swayidle
pkill -f wayland-pipewire-idle-inhibit

## systemd / D-Bus environment — must be ready before any D-Bus clients launch
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP XDG_RUNTIME_DIR
dbus-update-activation-environment --systemd DISPLAY WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP XDG_RUNTIME_DIR
systemctl --user start xdg-desktop-portal xdg-desktop-portal-wlr xdg-desktop-portal-gtk

## GTK dark theme — re-applied on every sway start so all GTK apps (incl.
## Chromium/Brave native UI, which queries the portal color-scheme) inherit it
gsettings set org.gnome.desktop.interface color-scheme prefer-dark 2>/dev/null || true
gsettings set org.gnome.desktop.interface gtk-theme Adwaita-dark 2>/dev/null || true

# Autostart applications
## Notification daemon
swaync -c ~/.config/sway/swaync/config.json -s ~/.config/sway/swaync/style.css &

## Let swaync register on D-Bus before waybar queries it
sleep 1

## Status bar
waybar -c ~/.config/sway/waybar/config-glyphs -s ~/.config/sway/waybar/style-glyphs.css &

## System tray / polkit
lxpolkit &

## clipse listener daemon — records clipboard history for the TUI picker.
## `clipse -listen` exits after spawning two DETACHED `wl-paste --watch ...
## clipse --wl-store` listeners. Restarted unconditionally on every reload:
## -listen's internal KillExisting reaps stragglers so the count converges
## back to 2 (never stacks; also covers partial death). The restart
## re-delivers the current selection, but with allowDuplicates:false that
## adds no unseen ident, so the toast watcher (seen-set predicate) stays
## silent. Unconditional restart also picks up config.json changes
## (e.g. maxHistory) without manual intervention.
clipse -listen &
sleep 1

## "Copied" toast watcher — reacts to clipse's OWN history via inotifywait
## (exe = inotifywait, invisible to clipse -listen's KillExisting() → survives).
## Unconditional kill-then-start on every reload: a fresh watcher seeds its
## seen-set from live history with no toast, so restarts are silent by
## construction (only genuinely new arrivals toast). Scoped pkill patterns
## keep exactly one instance: the script plus its blocking inotifywait child
## (which would otherwise linger until the next fs event and double-toast).
## Ordered after the clipse restart so the seed reflects post-restart history.
pkill -f "sway/scripts/clipboard-toast.py" 2>/dev/null || true
pkill -f "sway/scripts/clipboard-toast.sh" 2>/dev/null || true  # legacy bash predecessor — drop once deployed everywhere
pkill -f "inotifywait.*clipse" 2>/dev/null || true
setsid ~/.config/sway/scripts/clipboard-toast.py &>/dev/null &

## System alert monitor (temp, VRAM)
~/.config/sway/scripts/sys-alert &
notify-send -a "system-monitors" -t 3000 -u low "System Monitors" "Active: GPU temp, VRAM usage, CPU temperature"

## Audio-based idle inhibition — prevents swayidle from firing while any
## PipeWire stream is active (replaces the old idle-guard.sh audio checks)
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-1}
export SWAYSOCK=${SWAYSOCK:-$(ls /tmp/sway-ipc.$(id -u).*.sock 2>/dev/null | head -1)}
"$HOME/.cargo/bin/wayland-pipewire-idle-inhibit" &

## Idle management (dim, lock, DPMS, suspend)
swayidle -w \
    timeout 90   "$HOME/.config/sway/scripts/brightness-dim.sh dim" \
                   resume "$HOME/.config/sway/scripts/brightness-dim.sh restore" \
    timeout 180  'pidof gtklock || gtklock -d' \
    timeout 300  'swaymsg "output * dpms off"' \
                   resume 'swaymsg "output * dpms on"' \
    timeout 600  'systemctl suspend' \
    before-sleep 'pidof gtklock || gtklock -d' \
    after-resume 'swaymsg "output * enable"' &

## Bluetooth
## Note: BT starts off at every boot — the radio stays soft-blocked
## (Debian default). Blueman won't auto-power it on.
## Use the Blueman tray icon to turn it on when needed.
gsettings set org.blueman.plugins.powermanager auto-power-on false 2>/dev/null || true
blueman-applet &
## Bluetooth idle monitor — powers off after 3 min with no connected devices
systemctl --user enable --now bluetooth-idle-monitor.timer 2>/dev/null || true
## MPRIS mutual-exclusivity watcher — pause other players when one starts
systemctl --user enable --now mpris-exclusive.service 2>/dev/null || true
