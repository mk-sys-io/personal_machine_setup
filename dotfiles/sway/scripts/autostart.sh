#!/bin/bash

# Autostart applications
## Notification daemon
pkill -x swaync
swaync -c ~/.config/sway/swaync/config.json -s ~/.config/sway/swaync/style.css &

## Status bar
pkill -x waybar
waybar -c ~/.config/sway/waybar/config-glyphs -s ~/.config/sway/waybar/style-glyphs.css &

## System tray / polkit
lxpolkit &

## Clipboard history watcher
wl-paste --watch cliphist store &

## System alert monitor (temp, VRAM)
pkill -x sys-alert
~/.config/sway/scripts/sys-alert &

## systemd / D-Bus environment for portals
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP
dbus-update-activation-environment --systemd DISPLAY WAYLAND_DISPLAY SWAYSOCK XDG_CURRENT_DESKTOP
systemctl --user start xdg-desktop-portal-wlr
