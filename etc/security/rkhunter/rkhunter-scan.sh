#!/usr/bin/env bash
# ===========================================================================
# rkhunter-scan.sh — Weekly rkhunter scan wrapper (run by anacron as root)
#
# Deployed by lib/55-security.sh to /etc/cron.weekly/rkhunter-scan.
# Runs the rootkit scan and sends a clickable desktop notification to the
# user's swaync session with the result. Clicking the notification opens the
# rkhunter log in kitty.
#
# Runs as root via cron/anacron — independent of sudo group membership and
# the root password (Ark lockdown does not affect it).
#
# ALWAYS exits 0: the desktop notification is the sole failure signal, so
# cron/anacron does not record a failure or mail root. Errors are surfaced
# in the notification, never swallowed silently.
#
# Notifications are sent via notify-user (see etc/security/rkhunter/notify-user.sh)
# running in the user's systemd session. The DBus session bus rejects root
# connections, so the notification must run as the user (uid 1000) — hence
# systemd-run --user --machine=mike@.host. This is independent of sudo.
# ===========================================================================

set -uo pipefail

RKHUNTER=/usr/bin/rkhunter
NOTIFY_USER=/usr/local/bin/notify-user

notify() {
    local urgency="$1" summary="$2" body="$3"
    # systemd-run is async by default, so the cron job does not block. The
    # notify-user helper (running as the user) handles the clickable action
    # and logs any notification failure to ~/.cache/notify-user.log.
    # Redirect stdout/stderr: systemd-run prints "Running as unit: ..." which
    # cron would otherwise capture and mail to the user.
    systemd-run --user --machine=mike@.host --collect \
        "$NOTIFY_USER" "$urgency" "$summary" "$body" >/dev/null 2>&1
}

# Run the scan. --cronjob implies --check --nocolors --skip-keypress.
# --report-warnings-only prints only warnings to stdout; full detail goes to
# the log via --appendlog.
warnings=$("$RKHUNTER" --cronjob --report-warnings-only --appendlog 2>&1)
rc=$?

# Make the log readable by the user (mike): the click-to-open action runs as
# the user (not root) to reach the Wayland display, but rkhunter's log is
# created 0600 root by the restrictive cron umask. rkhunter's default is 0644;
# restore that so the click can open it directly.
chmod 644 /var/log/rkhunter.log 2>/dev/null || true

# rkhunter exit codes: 0 = no warnings, 1 = warnings found, 2 = error.
# Only treat 2 (or other) as a hard failure — 1 means warnings, which is a
# normal completion, not a scan failure.
if (( rc >= 2 )); then
    notify "critical" "rkhunter scan FAILED" \
        "rkhunter exited with status $rc — click to view log"
    exit 0
fi

count=$(printf '%s\n' "$warnings" | grep -c 'Warning' || true)

if (( rc == 1 || count > 0 )); then
    notify "normal" "rkhunter scan complete — $count warning(s)" \
        "Click to view /var/log/rkhunter.log"
else
    notify "low" "rkhunter scan complete" "No warnings found"
fi

exit 0
