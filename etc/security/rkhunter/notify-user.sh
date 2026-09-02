#!/usr/bin/env bash
# ===========================================================================
# notify-user — send a clickable desktop notification as the invoking user.
#
# Runs in the user's systemd session, invoked by root-run scripts via:
#   systemd-run --user --machine=mike@.host --collect notify-user <urgency> <summary> <body>
#
# Why this exists: the DBus session bus (/run/user/<uid>/bus) actively closes
# connections from root (uid 0) — a user's session bus only serves that user's
# processes. Running via systemd-run --user makes this execute as the user
# (uid 1000), so it can connect to the session bus legitimately. This is
# independent of sudo group membership and unaffected by Ark removing sudo.
#
# NOTE: This helper currently lives under etc/security/rkhunter/ because it is
# deployed by the security module. If this notification mechanism is reused
# outside the security setup, consider relocating it (e.g. to tools/) so it is
# deployed by the general tools loop instead.
# ===========================================================================

set -uo pipefail

NOTIFY_UID="1000"
RUNTIME_DIR="/run/user/${NOTIFY_UID}"
LOG="${HOME}/.cache/notify-user.log"

export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${RUNTIME_DIR}/bus"
# Auto-discover the compositor socket (e.g. wayland-1); the number can change
# across compositor restarts, so never hardcode it.
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-$(ls ${RUNTIME_DIR}/wayland-* 2>/dev/null | head -1 | xargs -r basename)}"

urgency="$1"
summary="$2"
body="$3"

# Register the default action so clicking the notification opens the log. The
# click handling itself lives in swaync's scripts mechanism (run-on: "action",
# see dotfiles/sway/swaync/config.json) — not here — because swaync runs the
# click command in the user's graphical session with the correct environment.
notify-send --urgency="$urgency" \
    --action=default="Open log" \
    --app-name="rkhunter" \
    "$summary" "$body" 2>>"$LOG"
rc=$?

if (( rc != 0 )); then
    printf '%s notify-send failed (rc=%s)\n' "$(date '+%F %T')" "$rc" >> "$LOG"
    exit 0
fi

exit 0
