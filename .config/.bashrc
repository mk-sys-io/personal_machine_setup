# --- linux_setup additions ---

alias help="zed ~/linux_setup/keybindings.md 2>/dev/null || nano ~/linux_setup/keybindings.md"

if [ -z "${DISPLAY}" ] && [ -z "${WAYLAND_DISPLAY}" ] && [ "${XDG_VTNR}" -eq 1 ]; then
    exec sway
fi
