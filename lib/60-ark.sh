#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 60-ark.sh — System lockdown deployment
#
# Deploys security hardening: nftables, sudoers, polkit, ark utility,
# internet network namespace, browser policy lockdown.
# Runs as root (sudo). Replaces Makefile.lockdown with direct shell.
#
# set -euo pipefail handles hard failures (exit 1). Uses deploy_file helper
# for the repeated cp+chmod+chown pattern. No glob expansion on system paths.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
set -a
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
    local backup_dir="/tmp/ark-backup-$(date +%s)"
    mkdir -p "$backup_dir"
    log "Backing up to $backup_dir"
    [[ -f /etc/nftables.conf ]] && cp /etc/nftables.conf "$backup_dir/" || true
    [[ -f /etc/sudoers.d/99-mike-tools ]] && cp /etc/sudoers.d/99-mike-tools "$backup_dir/" || true
    [[ -d "$ARK_DATA_PATH" ]] && cp -r "$ARK_DATA_PATH" "$backup_dir/" || true
    log_ok "Backup complete"
}

# ---------------------------------------------------------------------------
# 2. Adapters (ark helper scripts)
# ---------------------------------------------------------------------------

deploy_adapters() {
    log_step "Deploying adapters"
    mkdir -p "$ARK_LIB_PATH"
    deploy_file "$REPO_ROOT/etc/ark/adapters/discover-session.py" "$ARK_LIB_PATH/discover-session.py"
    deploy_file "$REPO_ROOT/etc/ark/adapters/clipboard-clear.sh"  "$ARK_LIB_PATH/clipboard-clear.sh" 755
    deploy_file "$REPO_ROOT/etc/ark/adapters/terminal"            "$ARK_LIB_PATH/terminal"            755
    log_ok "Adapters deployed to $ARK_LIB_PATH"
}

# ---------------------------------------------------------------------------
# 3. Lockdown scripts
# ---------------------------------------------------------------------------

deploy_ark_scripts() {
    log_step "Deploying ark scripts"
    mkdir -p "$ARK_DATA_PATH/scripts"
    deploy_file "$REPO_ROOT/ark/scripts/enter-internet-netns" "$ARK_DATA_PATH/scripts/enter-internet-netns" 755
    deploy_file "$REPO_ROOT/ark/scripts/cask_lib.py"         "$ARK_DATA_PATH/scripts/cask_lib.py"
    deploy_file "$REPO_ROOT/ark/scripts/cask_system.py"      "$ARK_DATA_PATH/scripts/cask_system.py"
    deploy_file "$REPO_ROOT/ark/scripts/immutable_lib.py"    "$ARK_DATA_PATH/scripts/immutable_lib.py"
    deploy_file "$REPO_ROOT/ark/scripts/mcask.py"             "$ARK_DATA_PATH/scripts/mcask.py"         755
    deploy_file "$REPO_ROOT/ark/scripts/uncask.py"           "$ARK_DATA_PATH/scripts/uncask.py"         755
    deploy_file "$REPO_ROOT/lib/python/opslog.py"            "$ARK_DATA_PATH/scripts/opslog.py"
    deploy_file "$REPO_ROOT/ark/scripts/setup-internet-netns.sh" "$ARK_DATA_PATH/scripts/setup-internet-netns.sh" 755
    deploy_file "$REPO_ROOT/ark/scripts/generate-policies.sh"    "$ARK_DATA_PATH/scripts/generate-policies.sh"    755
    deploy_file "$REPO_ROOT/ark/scripts/generate-dnsmasq.sh"     "$ARK_DATA_PATH/scripts/generate-dnsmasq.sh"     755
    deploy_file "$REPO_ROOT/ark/scripts/generate-nftables.sh"    "$ARK_DATA_PATH/scripts/generate-nftables.sh"    755
    deploy_file "$REPO_ROOT/ark/scripts/lockdown.sh"             "$ARK_DATA_PATH/scripts/lockdown.sh"             755
    deploy_file "$REPO_ROOT/ark/scripts/mode.py"                 "$ARK_DATA_PATH/scripts/mode.py"                 755
    deploy_file "$REPO_ROOT/ark/scripts/ark.py"                "$ARK_DATA_PATH/scripts/ark.py"                755
    log_ok "Ark scripts deployed"
}

# ---------------------------------------------------------------------------
# 4. Domain lists
# ---------------------------------------------------------------------------

deploy_ark_domains() {
    log_step "Deploying domain lists"
    mkdir -p "$ARK_DATA_PATH/domains/locked" "$ARK_DATA_PATH/domains/focused"
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/infra.txt"   "$ARK_DATA_PATH/domains/locked/infra.txt"   640
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/base.txt"    "$ARK_DATA_PATH/domains/locked/base.txt"    640
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/session.txt" "$ARK_DATA_PATH/domains/locked/session.txt" 640
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/deny.txt"    "$ARK_DATA_PATH/domains/locked/deny.txt"    640
    # Back-compat root copies — keep until P12 (ark.py/generate-dnsmasq.sh/
    # lockdown.sh/blocklist.py still read the old paths).
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/infra.txt"   "$ARK_DATA_PATH/infra.txt"   640
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/base.txt"    "$ARK_DATA_PATH/base.txt"    640
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/session.txt" "$ARK_DATA_PATH/session.txt" 640
    deploy_file "$REPO_ROOT/etc/ark/domains/locked/deny.txt"    "$ARK_DATA_PATH/deny.txt"    640
    log_ok "Domain lists deployed"
}

# ---------------------------------------------------------------------------
# 5. Sudoers
# ---------------------------------------------------------------------------

deploy_sudoers() {
    log_step "Deploying sudoers"
    mkdir -p /etc/sudoers.d
    deploy_file "$REPO_ROOT/etc/ark/sudoers/99-mike-tools" /etc/sudoers.d/99-mike-tools 440
    log_ok "Sudoers deployed"
}

# ---------------------------------------------------------------------------
# 6. nftables
# ---------------------------------------------------------------------------

deploy_nftables() {
    log_step "Deploying nftables"
    deploy_file "$REPO_ROOT/etc/ark/nftables/nftables.conf.base"   /etc/nftables.conf
    deploy_file "$REPO_ROOT/etc/ark/nftables/nftables.conf.base"   "$ARK_DATA_PATH/nftables.conf.base" 640
    deploy_file "$REPO_ROOT/etc/ark/nftables/nftables.conf.restricted" "$ARK_DATA_PATH/nftables.conf.restricted" 640
    log_ok "nftables deployed"
}

# ---------------------------------------------------------------------------
# 7. Polkit
# ---------------------------------------------------------------------------

deploy_polkit() {
    log_step "Deploying polkit rules"
    mkdir -p /etc/polkit-1/rules.d
    deploy_file "$REPO_ROOT/etc/ark/polkit/99-internet-lockdown.rules" /etc/polkit-1/rules.d/99-internet-lockdown.rules
    log_ok "Polkit rules deployed"
}

# ---------------------------------------------------------------------------
# 8. DNS resolv (internet network namespace)
# ---------------------------------------------------------------------------

deploy_resolv() {
    log_step "Deploying netns resolv"
    mkdir -p /etc/netns/internet-netns
    deploy_file "$REPO_ROOT/etc/ark/resolv/internet-netns.resolv.conf" /etc/netns/internet-netns/resolv.conf
    log_ok "Netns resolv.conf deployed"
}

# ---------------------------------------------------------------------------
# 9. Systemd service
# ---------------------------------------------------------------------------

deploy_systemd() {
    log_step "Deploying systemd service"
    deploy_file "$REPO_ROOT/etc/ark/systemd/internet-netns.service" /etc/systemd/system/internet-netns.service
    log_ok "internet-netns.service deployed"
}

# ---------------------------------------------------------------------------
# 10. Sysctl
# ---------------------------------------------------------------------------

deploy_sysctl() {
    log_step "Deploying sysctl"
    mkdir -p /etc/sysctl.d
    deploy_file "$REPO_ROOT/etc/ark/sysctl.d/99-internet-netns.conf" /etc/sysctl.d/99-internet-netns.conf
    sysctl --system > /dev/null 2>&1
    log_ok "ip_forward=1 enabled"
}

# ---------------------------------------------------------------------------
# 11. Bin scripts (enter-internet-netns, mcask, uncask, etc.)
# ---------------------------------------------------------------------------

deploy_bin_scripts() {
    log_step "Deploying bin scripts"
    # remove the old cask-mobile bin name (superseded by mcask)
    rm -f "$ARK_BIN_PATH/cask-mobile"
    deploy_file "$REPO_ROOT/ark/scripts/enter-internet-netns" "$ARK_BIN_PATH/enter-internet-netns" 755
    deploy_file "$REPO_ROOT/ark/scripts/mcask.py"              "$ARK_BIN_PATH/mcask"                 755
    deploy_file "$REPO_ROOT/ark/scripts/uncask.py"            "$ARK_BIN_PATH/uncask"                755
    deploy_file "$REPO_ROOT/ark/scripts/setup-internet-netns.sh" "$ARK_LIB_PATH/setup-internet-netns.sh" 755
    deploy_file "$REPO_ROOT/ark/scripts/lockdown.sh"         "$ARK_BIN_PATH/lockdown"             755
    log_ok "Bin scripts deployed to $ARK_BIN_PATH"
}

# ---------------------------------------------------------------------------
# 12. Template substitution (gomplate)
# ---------------------------------------------------------------------------

subst_templates() {
    log_step "Template substitution (gomplate)"

    local count=0
    while IFS= read -r -d '' f; do
        gomplate -o "$f" -f "$f"
        count=$((count + 1))
    done < <(
        grep -rlZ '{{ \.Env\.' $ARK_RENDER_PATHS 2>/dev/null
    )

    local remaining
    remaining=$(grep -rl '{{ \.Env\.' $ARK_RENDER_PATHS 2>/dev/null || true)
    if [[ -n "$remaining" ]]; then
        log_error "Raw templates remain after substitution:"
        printf '  %s\n' "$remaining"
        return 1
    fi

    log_ok "Rendered $count files"
}

# ---------------------------------------------------------------------------
# 13. Browser policy templates
# ---------------------------------------------------------------------------

deploy_browser_policies() {
    log_step "Browser policies"
    deploy_file "$REPO_ROOT/dotfiles/brave/policy.json.template"     "$ARK_DATA_PATH/brave-policy.json.template"     640
    deploy_file "$REPO_ROOT/dotfiles/firefox/policies.json.template" "$ARK_DATA_PATH/firefox-policies.json.template" 640
    log "Generating browser policies..."
    "$ARK_DATA_PATH/scripts/generate-policies.sh"
    log_ok "Browser policies deployed"
}

# ---------------------------------------------------------------------------
# 14. Lockdown ownership
# ---------------------------------------------------------------------------

deploy_ark_perms() {
    log_step "Setting ark permissions"
    chattr -i "$ARK_DATA_PATH/mode" 2>/dev/null || true
    # Migration: old seal dir → cask (S9). One-time — runs only while /opt/ark/seal exists.
    if [[ -d "$ARK_DATA_PATH/seal" && ! -e "$ARK_DATA_PATH/cask" ]]; then
        chattr -i "$ARK_DATA_PATH/seal/system.sealed" "$ARK_DATA_PATH/seal/mobile.sealed" "$ARK_DATA_PATH/seal/metadata.json" 2>/dev/null || true
        mv "$ARK_DATA_PATH/seal" "$ARK_DATA_PATH/cask"
        # Rename casked files to the new-world names (inside the one-time guard).
        if [[ -f "$ARK_DATA_PATH/cask/system.sealed" ]]; then
            mv "$ARK_DATA_PATH/cask/system.sealed" "$ARK_DATA_PATH/cask/system.cask"
        fi
        if [[ -f "$ARK_DATA_PATH/cask/mobile.sealed" ]]; then
            mv "$ARK_DATA_PATH/cask/mobile.sealed" "$ARK_DATA_PATH/cask/mobile.cask"
        fi
        log "Migrated $ARK_DATA_PATH/seal → $ARK_DATA_PATH/cask"
    fi
    chattr -i "$ARK_DATA_PATH/cask/system.cask" "$ARK_DATA_PATH/cask/mobile.cask" "$ARK_DATA_PATH/cask/metadata.json" 2>/dev/null || true
    chattr -i "$ARK_DATA_PATH/domains/.blocklist-registry.json" 2>/dev/null || true
    chown -R root:root "$ARK_DATA_PATH"
    chmod 750 "$ARK_DATA_PATH"
    chmod 750 "$ARK_BIN_PATH/lockdown"
    chown root:root "$ARK_DATA_PATH/cask" 2>/dev/null || true
    chmod 750 "$ARK_DATA_PATH/cask" 2>/dev/null || true
    mkdir -p "$ARK_DATA_PATH/logs"
    chown root:root "$ARK_DATA_PATH/logs"
    chmod 750 "$ARK_DATA_PATH/logs"
    chattr +i "$ARK_DATA_PATH/mode" 2>/dev/null || true
    chattr +i "$ARK_DATA_PATH/cask/system.cask" "$ARK_DATA_PATH/cask/mobile.cask" "$ARK_DATA_PATH/cask/metadata.json" 2>/dev/null || true

    # Self-heal any crash-interrupted cask/mode writes, then verify flags.
    # The blocklist registry is NOT +i'd here — it owns its flag (never set).
    python3 "$ARK_DATA_PATH/scripts/immutable_lib.py" repair \
        "$ARK_DATA_PATH/mode" \
        "$ARK_DATA_PATH/cask/system.cask" \
        "$ARK_DATA_PATH/cask/mobile.cask" \
        "$ARK_DATA_PATH/cask/metadata.json" \
        || log_error "immutable_lib repair reported failures — verifying below"

    local imm_fail=0
    for f in "$ARK_DATA_PATH/mode" \
             "$ARK_DATA_PATH/cask/system.cask" \
             "$ARK_DATA_PATH/cask/mobile.cask" \
             "$ARK_DATA_PATH/cask/metadata.json"; do
        if [[ -e "$f" ]] && ! python3 "$ARK_DATA_PATH/scripts/immutable_lib.py" is "$f" | grep -q IMMUTABLE; then
            log_error "Immutable flag missing on $f"
            imm_fail=1
        fi
    done
    if [[ "$imm_fail" -eq 1 ]]; then
        log_error "Immutable-flag verification failed — fix the flags, then re-run deploy"
        return 1
    fi
    log_ok "Lockdown permissions set"
}

# ---------------------------------------------------------------------------
# 15. Aegis tools (blocklist manager)
# ---------------------------------------------------------------------------

deploy_ark() {
    log_step "Deploying ark tools"
    deploy_file "$REPO_ROOT/ark/scripts/blocklist.py" "$ARK_BIN_PATH/blocklist" 755
    deploy_file "$REPO_ROOT/ark/scripts/ark.py" "$ARK_BIN_PATH/ark" 755
    log_ok "Ark tools deployed to $ARK_BIN_PATH"
}

# ---------------------------------------------------------------------------
# 16. Blocklist deployment
# ---------------------------------------------------------------------------

deploy_blocklist() {
    log_step "Deploying blocklist"

    local custom_src="$REPO_ROOT/etc/ark/domains/focused/blocklist-custom.txt"
    local custom_dst="$ARK_DATA_PATH/domains/focused/blocklist-custom.txt"

    if [[ ! -f "$custom_src" ]]; then
        log_error "Custom blocklist source missing: $custom_src"
        return 1
    fi

    mkdir -p "$ARK_DATA_PATH/domains/focused"
    deploy_file "$custom_src" "$custom_dst" 640
    # Back-compat root copy — keep until P12 (blocklist.py still reads the old path).
    deploy_file "$custom_src" "$ARK_DATA_PATH/domains/blocklist-custom.txt" 640

    if [[ ! -f "$custom_dst" ]]; then
        log_error "Custom blocklist deployment failed: $custom_dst"
        return 1
    fi
    log "Custom blocklist: $(wc -l < "$custom_dst") entries → $custom_dst"

    if [[ -n "${BLOCKLIST_URLS:-}" ]]; then
        for url in $BLOCKLIST_URLS; do
            log "Downloading: $url"
            blocklist download "$url"
        done
    else
        log_warn "BLOCKLIST_URLS not set — no upstream blocklists downloaded"
    fi

    log "Running blocklist generate..."
    if ! blocklist generate; then
        log_error "blocklist generate failed"
        return 1
    fi

    local hosts="$ARK_DATA_PATH/domains/blocklist.hosts"
    if [[ ! -f "$hosts" ]]; then
        log_error "blocklist.hosts not created after generate"
        log_error "Source files in domains/: $(ls "$ARK_DATA_PATH/domains/" 2>&1)"
        return 1
    fi
    log_ok "Blocklist deployed ($(wc -l < "$hosts") hosts → $hosts)"
}

# ---------------------------------------------------------------------------
# 17. Validation
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
# 18. Reload
# ---------------------------------------------------------------------------

reload_services() {
    log_step "Reloading services"
    systemctl daemon-reload
    systemctl enable --now internet-netns.service
    systemctl enable --now dnsmasq
    systemctl restart nftables 2>/dev/null || true
    log_ok "Services reloaded"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "System lockdown"

backup_existing
deploy_adapters
deploy_ark_scripts
deploy_ark_domains
deploy_sudoers
deploy_nftables
deploy_polkit
deploy_resolv
deploy_systemd
deploy_sysctl
deploy_bin_scripts
deploy_ark
subst_templates
deploy_blocklist
deploy_browser_policies
deploy_ark_perms
validate_configs
reload_services

log_step "Lockdown complete"
exit 0
