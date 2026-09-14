#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 55-security.sh — Security tools configuration
#
# Deploys auditd rules/config, fail2ban enablement, and rkhunter setup.
# Packages installed by lib/20-packages.sh (apt.txt).
# set -euo pipefail handles hard failures (exit 1). Functions return 0 on
# skip (tool not available) which is not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

SECURITY_SRC="$REPO_ROOT/etc/security"

# ---------------------------------------------------------------------------
# 1. auditd — kernel-level audit logging
# ---------------------------------------------------------------------------

setup_auditd() {
    if ! pkg_installed auditd; then
        log_warn "auditd: package not installed — skipping"
        return 0
    fi

    log_step "auditd"

    # Deploy audit rules
    local rules_src="$SECURITY_SRC/auditd/rules.d/10-security.rules"
    local rules_dst="/etc/audit/rules.d/10-security.rules"
    sudo mkdir -p /etc/audit/rules.d
    if ! sudo cmp -s "$rules_src" "$rules_dst" 2>/dev/null; then
        sudo cp "$rules_src" "$rules_dst"
        sudo chmod 640 "$rules_dst"
        log "auditd: rules deployed to $rules_dst"
    fi

    # Deploy daemon config
    local conf_src="$SECURITY_SRC/auditd/auditd.conf"
    local conf_dst="/etc/audit/auditd.conf"
    if ! sudo cmp -s "$conf_src" "$conf_dst" 2>/dev/null; then
        sudo cp "$conf_src" "$conf_dst"
        sudo chmod 644 "$conf_dst"
        log "auditd: config deployed to $conf_dst"
    fi

    # Load rules (sudo works whether or not the module runs as root)
    sudo augenrules --load 2>/dev/null || log_warn "auditd: augenrules --load failed"

    # Enable and start service
    if cmd_exists systemctl; then
        sudo systemctl enable auditd 2>/dev/null || true
        sudo systemctl start auditd 2>/dev/null || true
    fi

    log_ok "auditd: configured, rules loaded"
}

# ---------------------------------------------------------------------------
# 2. fail2ban — brute-force login detection
# ---------------------------------------------------------------------------

setup_fail2ban() {
    if ! cmd_exists fail2ban-client; then
        log_warn "fail2ban: fail2ban-client not found — skipping"
        return 0
    fi

    log_step "fail2ban"

    # Debian defaults are fine — no custom jails needed (Ark handles network)
    if cmd_exists systemctl; then
        sudo systemctl enable fail2ban 2>/dev/null || true
        sudo systemctl start fail2ban 2>/dev/null || true
    fi

    log_ok "fail2ban: enabled with default config"
}

# ---------------------------------------------------------------------------
# 3. rkhunter — rootkit signature scanner
# ---------------------------------------------------------------------------

setup_rkhunter() {
    if ! cmd_exists rkhunter; then
        log_warn "rkhunter: rkhunter not found — skipping"
        return 0
    fi

    log_step "rkhunter"

    # Deploy local config overrides
    local conf_src="$SECURITY_SRC/rkhunter/rkhunter.conf.local"
    local conf_dst="/etc/rkhunter.conf.local"
    if ! sudo cmp -s "$conf_src" "$conf_dst" 2>/dev/null; then
        sudo cp "$conf_src" "$conf_dst"
        sudo chmod 644 "$conf_dst"
        log "rkhunter: local config deployed to $conf_dst"
    fi

    # Deploy cron defaults
    local default_src="$SECURITY_SRC/rkhunter/default-rkhunter"
    local default_dst="/etc/default/rkhunter"
    if ! sudo cmp -s "$default_src" "$default_dst" 2>/dev/null; then
        sudo cp "$default_src" "$default_dst"
        sudo chmod 644 "$default_dst"
        log "rkhunter: cron defaults deployed to $default_dst"
    fi

    # Deploy weekly scan wrapper (runs via anacron, catches up missed runs)
    local scan_src="$SECURITY_SRC/rkhunter/rkhunter-scan.sh"
    local scan_dst="/etc/cron.weekly/rkhunter-scan"
    if ! sudo cmp -s "$scan_src" "$scan_dst" 2>/dev/null; then
        sudo cp "$scan_src" "$scan_dst"
        sudo chmod 755 "$scan_dst"
        log "rkhunter: weekly scan wrapper deployed to $scan_dst"
    fi

    # Deploy notify-user helper (runs in the user's systemd session to send
    # notifications — the DBus session bus rejects root connections)
    local notify_src="$SECURITY_SRC/rkhunter/notify-user.sh"
    local notify_dst="/usr/local/bin/notify-user"
    if ! sudo cmp -s "$notify_src" "$notify_dst" 2>/dev/null; then
        sudo cp "$notify_src" "$notify_dst"
        sudo chmod 755 "$notify_dst"
        log "rkhunter: notify-user helper deployed to $notify_dst"
    fi

    # Update signature database
    sudo rkhunter --update --nocolors --quiet 2>/dev/null || log_warn "rkhunter: --update failed"
    log "rkhunter: database updated"

    # Build file property baseline
    sudo rkhunter --propupd --nocolors --quiet 2>/dev/null || log_warn "rkhunter: --propupd failed"
    log "rkhunter: baseline built"

    log_ok "rkhunter: configured, baseline set, weekly scan cron enabled"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "Security tools"

setup_auditd
setup_fail2ban
setup_rkhunter

log_step "Security tools complete"
exit 0
