#!/bin/bash

# Kill orphaned instances from prior sway reloads
for pid in $(pgrep -f "sway/scripts/autostart.sh"); do
    [ "$pid" != "$$" ] && kill "$pid" 2>/dev/null
done
pkill -x waybar
pkill -x swaync
pkill -x swaync-client
killall -w nwg-dock 2>/dev/null || true
pkill -x sys-alert

## systemd / D-Bus environment — must be ready before any D-Bus clients launch
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP
dbus-update-activation-environment --systemd DISPLAY WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP
systemctl --user start xdg-desktop-portal-wlr

# Autostart applications
## Notification daemon
swaync -c ~/.config/sway/swaync/config.json -s ~/.config/sway/swaync/style.css &

## Let swaync register on D-Bus before waybar queries it
sleep 1

## Status bar
waybar -c ~/.config/sway/waybar/config-glyphs -s ~/.config/sway/waybar/style-glyphs.css &

## Dock (auto-hide, bottom edge)
nwg-dock &

## System tray / polkit
lxpolkit &

## Clipboard history watcher
wl-paste --watch cliphist store &

## System alert monitor (temp, VRAM)
~/.config/sway/scripts/sys-alert &
notify-send -i hardware-sensors "System Monitors" "Active: GPU temp, VRAM usage, CPU temperature" -u low
