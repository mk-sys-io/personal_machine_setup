#!/bin/bash

# Kill orphaned instances from prior sway reloads
for pid in $(pgrep -f "sway/scripts/autostart.sh"); do
    [ "$pid" != "$$" ] && kill "$pid" 2>/dev/null
done
pkill -x waybar
pkill -x swaync
pkill -x swaync-client
pkill -x sys-alert
pkill -x swayidle
pkill -x wl-paste

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

## System tray / polkit
lxpolkit &

## Clipboard history watcher (restart on every sway reload)
wl-paste --watch ~/.config/sway/scripts/clipboard-watch.sh &

## System alert monitor (temp, VRAM)
~/.config/sway/scripts/sys-alert &
notify-send "System Monitors" "Active: GPU temp, VRAM usage, CPU temperature" -u low

## Idle management (dim, lock, DPMS, suspend)
## Timed actions go through idle-guard.sh, which skips them while any
## sink-input is Corked: no (audio playing) — see idle-guard.sh.
swayidle -w \
    timeout 90   "$HOME/.config/sway/scripts/idle-guard.sh dim" \
                   resume "$HOME/.config/sway/scripts/brightness-dim.sh restore" \
    timeout 180  "$HOME/.config/sway/scripts/idle-guard.sh lock" \
    timeout 300  "$HOME/.config/sway/scripts/idle-guard.sh dpms-off" \
                   resume 'swaymsg "output * dpms on"' \
    timeout 600  "$HOME/.config/sway/scripts/idle-guard.sh suspend" \
    before-sleep 'gtklock' \
    after-resume 'swaymsg "output * enable"' &

## Bluetooth
## Note: BT starts off at every boot — the radio stays soft-blocked
## (Debian default). Blueman won't auto-power it on.
## Use the Blueman tray icon to turn it on when needed.
gsettings set org.blueman.plugins.powermanager auto-power-on false 2>/dev/null || true
blueman-applet &
## Bluetooth idle monitor — powers off after 3 min with no connected devices
systemctl --user enable --now bluetooth-idle-monitor.timer 2>/dev/null || true
