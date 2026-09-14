#!/bin/bash
# Adapter: clear clipboard — swap clipboard tools here, not in cask.
# Runs as root; clears the TARGET_USER's history stores + live clipboard.
# Required env (set by cask/clipboard.py): TARGET_USER, HOME_DIR,
# XDG_DATA_HOME, XDG_CONFIG_HOME, WAYLAND_DISPLAY, XDG_RUNTIME_DIR.
# Without the target user the tools would clear root's own (empty) stores
# and log success while the user's history survives untouched.

TARGET_USER="${TARGET_USER:-}"
if [[ -z "$TARGET_USER" ]]; then
    echo "clipboard-clear: TARGET_USER not set" >&2
    exit 1
fi

ran=0
if command -v clipse &>/dev/null; then
    sudo -u "$TARGET_USER" env HOME="$HOME_DIR" XDG_CONFIG_HOME="$XDG_CONFIG_HOME" clipse -clear-all
    ran=1
fi
if command -v wl-copy &>/dev/null && [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
    sudo -u "$TARGET_USER" env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" wl-copy --clear
    ran=1
fi
[[ "$ran" -eq 1 ]] && exit 0
echo "clipboard-clear: no supported clipboard tool found" >&2
exit 1
