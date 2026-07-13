#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 60-lockdown.sh — System lockdown deployment
#
# Deploys security hardening: nftables, sudoers, polkit, allowlist utility,
# internet network namespace, browser policy lockdown.
# Runs as root (sudo). Replaces Makefile.lockdown with direct shell.
#
# set -euo pipefail handles hard failures (exit 1). Uses deploy_file helper
# for the repeated cp+chmod+chown pattern. No glob expansion on system paths.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/common.sh"
source "$REPO_ROOT/config.env"

# ---------------------------------------------------------------------------
# Helper: deploy_file SRC DST [MODE] [OWNER]
# ---------------------------------------------------------------------------

deploy_file() {
    local src="$1" dst="$2" mode="${3:-644}" owner="${4:-root:root}"
    cp "$src" "$dst"
    chmod "$mode" "$dst"
    chown "$owner" "$dst"
}

# ---------------------------------------------------------------------------
# 1. Backup
# ---------------------------------------------------------------------------

backup_existing() {
    local backup_dir="/tmp/lockdown-backup-$(date +%s)"
    mkdir -p "$backup_dir"
    log "Backing up to $backup_dir"
    [[ -f /etc/nftables.conf ]] && cp /etc/nftables.conf "$backup_dir/" || true
    [[ -f /etc/sudoers.d/99-mike-tools ]] && cp /etc/sudoers.d/99-mike-tools "$backup_dir/" || true
    [[ -d /opt/allowlist ]] && cp -r /opt/allowlist "$backup_dir/" || true
    log_ok "Backup complete"
}

# ---------------------------------------------------------------------------
# 2. Adapters (lockdown helper scripts)
# ---------------------------------------------------------------------------

deploy_adapters() {
    log_step "Deploying adapters"
    mkdir -p "$LOCKDOWN_LIB_PATH"
    deploy_file "$REPO_ROOT/lockdown/lib/discover-session.py" "$LOCKDOWN_LIB_PATH/discover-session.py"
    deploy_file "$REPO_ROOT/lockdown/lib/clipboard-clear.sh"  "$LOCKDOWN_LIB_PATH/clipboard-clear.sh" 755
    deploy_file "$REPO_ROOT/lockdown/lib/terminal"            "$LOCKDOWN_LIB_PATH/terminal"            755
    log_ok "Adapters deployed to $LOCKDOWN_LIB_PATH"
}

# ---------------------------------------------------------------------------
# 3. Allowlist scripts
# ---------------------------------------------------------------------------

deploy_allowlist_scripts() {
    log_step "Deploying allowlist scripts"
    mkdir -p "$ALLOWLIST_PATH/scripts"
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/enter-internet-netns" "$ALLOWLIST_PATH/scripts/enter-internet-netns" 755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/sem.py"              "$ALLOWLIST_PATH/scripts/sem.py"              755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/unseal.py"           "$ALLOWLIST_PATH/scripts/unseal.py"           755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/seal_lib.py"         "$ALLOWLIST_PATH/scripts/seal_lib.py"
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/seal.py"             "$ALLOWLIST_PATH/scripts/seal.py"             755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/setup-internet-netns.sh" "$ALLOWLIST_PATH/scripts/setup-internet-netns.sh" 755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/generate-policies.sh"    "$ALLOWLIST_PATH/scripts/generate-policies.sh"    755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/generate-dnsmasq.sh"     "$ALLOWLIST_PATH/scripts/generate-dnsmasq.sh"     755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/generate-nftables.sh"    "$ALLOWLIST_PATH/scripts/generate-nftables.sh"    755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/allowlist.sh"            "$ALLOWLIST_PATH/scripts/allowlist.sh"            755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/verify.sh"               "$ALLOWLIST_PATH/scripts/verify.sh"               755
    log_ok "Allowlist scripts deployed"
}

# ---------------------------------------------------------------------------
# 4. Allowlist domain lists
# ---------------------------------------------------------------------------

deploy_allowlist_domains() {
    log_step "Deploying allowlist domains"
    mkdir -p "$ALLOWLIST_PATH/domains"
    deploy_file "$REPO_ROOT/lockdown/allowlist/domains/infra.txt"   "$ALLOWLIST_PATH/allowlist.infra.txt"   640
    deploy_file "$REPO_ROOT/lockdown/allowlist/domains/base.txt"    "$ALLOWLIST_PATH/allowlist.base.txt"    640
    deploy_file "$REPO_ROOT/lockdown/allowlist/domains/session.txt" "$ALLOWLIST_PATH/allowlist.session.txt" 640
    deploy_file "$REPO_ROOT/lockdown/allowlist/domains/deny.txt"    "$ALLOWLIST_PATH/deny.txt"              640
    log_ok "Allowlist domains deployed"
}

# ---------------------------------------------------------------------------
# 5. Sudoers
# ---------------------------------------------------------------------------

deploy_sudoers() {
    log_step "Deploying sudoers"
    mkdir -p /etc/sudoers.d
    deploy_file "$REPO_ROOT/lockdown/sudoers/99-mike-tools" /etc/sudoers.d/99-mike-tools 440
    log_ok "Sudoers deployed"
}

# ---------------------------------------------------------------------------
# 6. nftables
# ---------------------------------------------------------------------------

deploy_nftables() {
    log_step "Deploying nftables"
    deploy_file "$REPO_ROOT/lockdown/nftables/nftables.conf.base"   /etc/nftables.conf
    deploy_file "$REPO_ROOT/lockdown/nftables/nftables.conf.locked" "$ALLOWLIST_PATH/nftables.conf.locked" 640
    log_ok "nftables deployed"
}

# ---------------------------------------------------------------------------
# 7. Polkit
# ---------------------------------------------------------------------------

deploy_polkit() {
    log_step "Deploying polkit rules"
    mkdir -p /etc/polkit-1/rules.d
    deploy_file "$REPO_ROOT/lockdown/polkit/99-internet-lockdown.rules" /etc/polkit-1/rules.d/99-internet-lockdown.rules
    log_ok "Polkit rules deployed"
}

# ---------------------------------------------------------------------------
# 8. DNS resolv (internet network namespace)
# ---------------------------------------------------------------------------

deploy_resolv() {
    log_step "Deploying netns resolv"
    mkdir -p /etc/netns/internet-netns
    deploy_file "$REPO_ROOT/lockdown/resolv/internet-netns.resolv.conf" /etc/netns/internet-netns/resolv.conf
    log_ok "Netns resolv.conf deployed"
}

# ---------------------------------------------------------------------------
# 9. Systemd service
# ---------------------------------------------------------------------------

deploy_systemd() {
    log_step "Deploying systemd service"
    deploy_file "$REPO_ROOT/lockdown/systemd/internet-netns.service" /etc/systemd/system/internet-netns.service
    log_ok "internet-netns.service deployed"
}

# ---------------------------------------------------------------------------
# 10. Sysctl
# ---------------------------------------------------------------------------

deploy_sysctl() {
    log_step "Deploying sysctl"
    mkdir -p /etc/sysctl.d
    deploy_file "$REPO_ROOT/lockdown/sysctl.d/99-internet-netns.conf" /etc/sysctl.d/99-internet-netns.conf
    sysctl --system > /dev/null 2>&1
    log_ok "ip_forward=1 enabled"
}

# ---------------------------------------------------------------------------
# 11. Bin scripts (enter-internet-netns, sem, unseal, etc.)
# ---------------------------------------------------------------------------

deploy_bin_scripts() {
    log_step "Deploying bin scripts"
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/enter-internet-netns" "$LOCKDOWN_BIN_PATH/enter-internet-netns" 755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/sem.py"              "$LOCKDOWN_BIN_PATH/sem"                  755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/unseal.py"           "$LOCKDOWN_BIN_PATH/unseal"               755
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/seal_lib.py"         "$LOCKDOWN_BIN_PATH/seal_lib.py"
    deploy_file "$REPO_ROOT/lockdown/allowlist/scripts/setup-internet-netns.sh" /usr/local/lib/setup-internet-netns.sh 755
    log_ok "Bin scripts deployed to $LOCKDOWN_BIN_PATH"
}

# ---------------------------------------------------------------------------
# 12. Template substitution (SUBST) — explicit file list, no globs
# ---------------------------------------------------------------------------

subst_templates() {
    log_step "Template substitution"
    local sed_expr="s|@USERNAME@|${USERNAME}|g; s|@OPENCODE_PATH@|${OPENCODE_PATH}|g; s|@OBSIDIAN_VAULT_PATH@|${OBSIDIAN_VAULT_PATH}|g; s|@ALLOWLIST_PATH@|${ALLOWLIST_PATH}|g; s|@LOCKDOWN_LIB_PATH@|${LOCKDOWN_LIB_PATH}|g; s|@LOCKDOWN_BIN_PATH@|${LOCKDOWN_BIN_PATH}|g; s|@DNS_PRIMARY@|${DNS_PRIMARY}|g; s|@DNS_SECONDARY@|${DNS_SECONDARY}|g; s|@NETNS_SUBNET@|${NETNS_SUBNET}|g; s|@NETNS_HOST@|${NETNS_HOST}|g; s|@NETNS_CLIENT@|${NETNS_CLIENT}|g; s|@USER_UID@|${USER_UID}|g"

    # Bin scripts
    sed -i "$sed_expr" \
        "$LOCKDOWN_BIN_PATH/enter-internet-netns" \
        "$LOCKDOWN_BIN_PATH/sem" \
        "$LOCKDOWN_BIN_PATH/unseal"

    # Allowlist scripts
    sed -i "$sed_expr" \
        "$ALLOWLIST_PATH/scripts/generate-policies.sh" \
        "$ALLOWLIST_PATH/scripts/generate-dnsmasq.sh" \
        "$ALLOWLIST_PATH/scripts/generate-nftables.sh" \
        "$ALLOWLIST_PATH/scripts/allowlist.sh" \
        "$ALLOWLIST_PATH/scripts/verify.sh" \
        "$ALLOWLIST_PATH/scripts/seal.py" \
        "$ALLOWLIST_PATH/scripts/setup-internet-netns.sh"

    # Config files — explicit paths, no globs
    sed -i "$sed_expr" \
        "$ALLOWLIST_PATH/nftables.conf.locked" \
        /etc/sudoers.d/99-mike-tools \
        /etc/polkit-1/rules.d/99-internet-lockdown.rules \
        /etc/nftables.conf \
        /etc/netns/internet-netns/resolv.conf \
        /etc/systemd/system/internet-netns.service \
        /etc/sysctl.d/99-internet-netns.conf \
        /usr/local/lib/setup-internet-netns.sh

    log_ok "Template substitution complete"
}

# ---------------------------------------------------------------------------
# 13. Browser policy templates
# ---------------------------------------------------------------------------

deploy_browser_policies() {
    log_step "Browser policies"
    deploy_file "$REPO_ROOT/dotfiles/brave/policy.json.template"     "$ALLOWLIST_PATH/brave-policy.json.template"     640
    deploy_file "$REPO_ROOT/dotfiles/firefox/policies.json.template" "$ALLOWLIST_PATH/firefox-policies.json.template" 640
    log "Generating browser policies..."
    "$ALLOWLIST_PATH/scripts/generate-policies.sh"
    log_ok "Browser policies deployed"
}

# ---------------------------------------------------------------------------
# 14. Allowlist ownership
# ---------------------------------------------------------------------------

set_allowlist_perms() {
    log_step "Setting allowlist permissions"
    chown -R root:root "$ALLOWLIST_PATH"
    chmod 750 "$ALLOWLIST_PATH"
    log_ok "Allowlist permissions set"
}

# ---------------------------------------------------------------------------
# 15. Validation
# ---------------------------------------------------------------------------

validate_configs() {
    log_step "Validating configs"
    if ! visudo -c -f /etc/sudoers.d/99-mike-tools 2>/dev/null; then
        log_error "Sudoers validation failed"
        return 1
    fi
    log_ok "Sudoers valid"

    log "Validating nftables..."
    if ! nft -c -f /etc/nftables.conf 2>/dev/null; then
        log_error "nftables validation failed"
        return 1
    fi
    log_ok "nftables valid"
}

# ---------------------------------------------------------------------------
# 16. Reload
# ---------------------------------------------------------------------------

reload_services() {
    log_step "Reloading services"
    systemctl daemon-reload
    systemctl restart nftables 2>/dev/null || true
    log_ok "Services reloaded"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "System lockdown"

backup_existing
deploy_adapters
deploy_allowlist_scripts
deploy_allowlist_domains
deploy_sudoers
deploy_nftables
deploy_polkit
deploy_resolv
deploy_systemd
deploy_sysctl
deploy_bin_scripts
subst_templates
deploy_browser_policies
set_allowlist_perms
validate_configs
reload_services

log_step "Lockdown complete"
exit 0
