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
# 1b. Retired-path check (fail closed — no silent rm in deploy)
# ---------------------------------------------------------------------------

check_retired_paths() {
    log_step "Checking for retired v1 paths"
    local found=0
    for p in \
        /etc/systemd/system/ark-transition.timer \
        /etc/systemd/system/ark-transition.service \
        "$ARK_DATA_PATH/scripts/ark-transition.sh"; do
        if [[ -e "$p" || -L "$p" ]]; then
            log_error "Retired path still present: $p"
            found=1
        fi
    done
    if [[ "$found" -eq 1 ]]; then
        log_error "Manual pre-install cleanup required: stop + disable the"
        log_error "ark-transition timer/service and delete the transition"
        log_error "script, then re-run install.sh (deploy never removes files)."
        return 1
    fi
    log_ok "No retired ark-transition paths present"
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
    deploy_file "$REPO_ROOT/ark/scripts/abort.py"            "$ARK_DATA_PATH/scripts/abort.py"
    deploy_file "$REPO_ROOT/ark/scripts/cask_lib.py"         "$ARK_DATA_PATH/scripts/cask_lib.py"
    deploy_file "$REPO_ROOT/ark/scripts/cask_system.py"      "$ARK_DATA_PATH/scripts/cask_system.py"
    deploy_file "$REPO_ROOT/ark/scripts/immutable_lib.py"    "$ARK_DATA_PATH/scripts/immutable_lib.py"
    deploy_file "$REPO_ROOT/ark/scripts/mcask.py"             "$ARK_DATA_PATH/scripts/mcask.py"         755
    deploy_file "$REPO_ROOT/ark/scripts/uncask.py"           "$ARK_DATA_PATH/scripts/uncask.py"         755
    deploy_file "$REPO_ROOT/lib/python/opslog.py"            "$ARK_DATA_PATH/scripts/opslog.py"
    deploy_file "$REPO_ROOT/ark/scripts/netmgr.py"           "$ARK_DATA_PATH/scripts/netmgr.py"         755
    while IFS= read -r f; do
        rel="${f#"$REPO_ROOT/ark/scripts/"}"
        mkdir -p "$ARK_DATA_PATH/scripts/$(dirname "$rel")"
        deploy_file "$f" "$ARK_DATA_PATH/scripts/$rel"
    done < <(find "$REPO_ROOT/ark/scripts/netmgr" -name '*.py' -type f | sort)
    deploy_file "$REPO_ROOT/ark/scripts/mode.py"                 "$ARK_DATA_PATH/scripts/mode.py"                 755
    deploy_file "$REPO_ROOT/ark/scripts/ark.py"                "$ARK_DATA_PATH/scripts/ark.py"                755
    deploy_file "$REPO_ROOT/etc/ark/immutable/immutable.sh"    "$ARK_DATA_PATH/scripts/immutable.sh"           755
    deploy_file "$REPO_ROOT/etc/ark/netns-exec-allowlist.txt"  "$ARK_DATA_PATH/netns-exec-allowlist.txt"         644
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
# 11. Bin scripts (mcask, uncask)
# ---------------------------------------------------------------------------

deploy_bin_scripts() {
    log_step "Deploying bin scripts"
    # remove the old cask-mobile bin name (superseded by mcask)
    rm -f "$ARK_BIN_PATH/cask-mobile"
    # remove the already-deployed legacy lockdown binary (archived at P14)
    rm -f "$ARK_BIN_PATH/lockdown"
    deploy_file "$REPO_ROOT/ark/scripts/mcask.py"              "$ARK_BIN_PATH/mcask"                 755
    deploy_file "$REPO_ROOT/ark/scripts/uncask.py"            "$ARK_BIN_PATH/uncask"                755
    log_ok "Bin scripts deployed to $ARK_BIN_PATH"
}

# ---------------------------------------------------------------------------
# 12. Template substitution (gomplate)
# ---------------------------------------------------------------------------

subst_templates() {
    log_step "Template substitution (gomplate)"

    # render_templates.py attempts every template, isolates per-file
    # gomplate failures (temp-file render + atomic replace — a failed file
    # keeps its raw {{ .Env.* }} markers and is never corrupted), and prints
    # a full report. Exit 0 = all rendered; 1 = some failed (report in
    # $output); anything else = the renderer itself crashed. Any failure
    # aborts the deploy — the post-deploy steps below import these files
    # and must never run against un-rendered templates.
    local output rc=0
    output=$(python3 "$SCRIPT_DIR/render_templates.py" $ARK_RENDER_PATHS 2>&1) || rc=$?

    if (( rc != 0 )); then
        if (( rc == 1 )); then
            log_error "Template substitution failed:"
        else
            log_error "render_templates.py crashed (exit $rc):"
        fi
        log_error "$output"
        return 1
    fi
    log_ok "$output"
}

# ---------------------------------------------------------------------------
# 12a. System DNS (netmgr)
# ---------------------------------------------------------------------------

deploy_system_dns() {
    log_step "System DNS (netmgr)"
    python3 "$ARK_DATA_PATH/scripts/netmgr.py" system setup-dns
    python3 "$ARK_DATA_PATH/scripts/netmgr.py" system setup-podman-dns
    # Exec-grant deploy — builds thin shims + inet and writes the generated
    # alias block into dotfiles/bashrc (source). Runs after subst_templates
    # (renders wrappers.py before it's imported). Live ~/.config/bashrc syncs
    # on the next make all/make dev (existing cp at Makefile:30).
    python3 "$ARK_DATA_PATH/scripts/netmgr.py" exec-grant deploy --bashrc "$REPO_ROOT/dotfiles/bashrc"
    log_ok "System DNS configured"
}

# ---------------------------------------------------------------------------
# 13. Browser policy templates
# ---------------------------------------------------------------------------

deploy_browser_policies() {
    log_step "Browser policies"
    deploy_file "$REPO_ROOT/dotfiles/brave/policy.json.template"     "$ARK_DATA_PATH/brave-policy.json.template"     640
    deploy_file "$REPO_ROOT/dotfiles/firefox/policies.json.template" "$ARK_DATA_PATH/firefox-policies.json.template" 640
    log "Generating browser policies..."
    python3 "$ARK_DATA_PATH/scripts/netmgr.py" deploy-policies
    log_ok "Browser policies deployed"
}

# ---------------------------------------------------------------------------
# 14. Lockdown ownership
# ---------------------------------------------------------------------------

deploy_ark_perms() {
    log_step "Setting ark permissions"
    # Raw chattr calls below are the deploy-time clear/set pattern. Exempt
    # from immutable_lib routing: repair_immutable (below) runs immediately
    # after and verifies + self-heals every flag, so raw calls are the fast
    # path with the module as the safety net.
    chattr -i "$ARK_DATA_PATH/mode" 2>/dev/null || true
    # Bootstrap /opt/ark/mode — create iff absent (root:root 0644; +i applied
    # below). An existing file is live state and is never touched or clobbered.
    if [[ ! -f "$ARK_DATA_PATH/mode" ]]; then
        printf 'unrestricted\n' > "$ARK_DATA_PATH/mode"
    fi
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
    chattr -i "$ARK_DATA_PATH/cask/system.cask" "$ARK_DATA_PATH/cask/mobile.cask" 2>/dev/null || true
    chown -R root:root "$ARK_DATA_PATH"
    chmod 750 "$ARK_DATA_PATH"
    chown root:root "$ARK_DATA_PATH/cask" 2>/dev/null || true
    chmod 750 "$ARK_DATA_PATH/cask" 2>/dev/null || true
    mkdir -p "$ARK_DATA_PATH/logs"
    chown root:root "$ARK_DATA_PATH/logs"
    chmod 750 "$ARK_DATA_PATH/logs"
    # Raw chattr: deploy-time set (repair_immutable below self-heals).
    chattr +i "$ARK_DATA_PATH/mode" 2>/dev/null || true
    chattr +i "$ARK_DATA_PATH/cask/system.cask" "$ARK_DATA_PATH/cask/mobile.cask" 2>/dev/null || true

    # Self-heal any crash-interrupted cask/mode writes, then verify flags.
    # metadata.json is write-hot fallback metadata — atomic replace, no +i.
    python3 "$ARK_DATA_PATH/scripts/immutable_lib.py" repair \
        "$ARK_DATA_PATH/mode" \
        "$ARK_DATA_PATH/cask/system.cask" \
        "$ARK_DATA_PATH/cask/mobile.cask" \
        || log_error "immutable_lib repair reported failures — verifying below"

    local imm_fail=0
    for f in "$ARK_DATA_PATH/mode" \
             "$ARK_DATA_PATH/cask/system.cask" \
             "$ARK_DATA_PATH/cask/mobile.cask"; do
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
# 14b. Timeshift (ark abort restore-hook + excludes + state dir)
# ---------------------------------------------------------------------------

deploy_timeshift() {
    log_step "Configuring timeshift for ark abort"

    # Abort state dir — the abort oracle (/opt/ark/state/enable.json) lives
    # here. 0750 root; NOT chattr +i'd (the restore-hook must be able to
    # delete enable.json, and the state dir is timeshift-excluded).
    mkdir -p "$ARK_DATA_PATH/state"
    chown root:root "$ARK_DATA_PATH/state"
    chmod 0750 "$ARK_DATA_PATH/state"

    # Restore-hook: run-parts requires a dotless name + exec bit. Written by
    # timeshift right before the forced reboot on an online restore; deletes
    # enable.json (breaks the re-abort reboot loop) + appends END abort: OK.
    mkdir -p /etc/timeshift/restore-hooks.d
    deploy_file "$REPO_ROOT/etc/ark/timeshift/99-ark-abort-log" \
        /etc/timeshift/restore-hooks.d/99-ark-abort-log 755

    # Idempotently inject the ark excludes. /opt/ark/logs must survive a
    # restore (the hook writes its marker there); /opt/ark/state must survive
    # so the abort gate semantics stay deterministic post-restore.
    local tsjson=/etc/timeshift/timeshift.json
    local tmpjson
    tmpjson="$(mktemp)"
    if ! jq --arg logs "$ARK_DATA_PATH/logs/***" --arg state "$ARK_DATA_PATH/state/***" \
        '.exclude = ((.exclude // []) + [$logs, $state] | unique)' "$tsjson" > "$tmpjson"; then
        rm -f "$tmpjson"
        log_error "timeshift exclude injection failed: $tsjson"
        return 1
    fi
    mv "$tmpjson" "$tsjson"
    chown root:root "$tsjson"
    chmod 644 "$tsjson"
    log_ok "Timeshift excludes: $ARK_DATA_PATH/logs/*** + $ARK_DATA_PATH/state/***"
}

# ---------------------------------------------------------------------------
# 15. Aegis tools (blocklist manager)
# ---------------------------------------------------------------------------

deploy_ark() {
    log_step "Deploying ark tools"
    deploy_file "$REPO_ROOT/etc/ark/netmgr-bin.py" "$ARK_BIN_PATH/netmgr" 755
    deploy_file "$REPO_ROOT/ark/scripts/ark.py" "$ARK_BIN_PATH/ark" 755
    log_ok "Ark tools deployed to $ARK_BIN_PATH"
}

# ---------------------------------------------------------------------------
# 16. Blocklist deployment
# ---------------------------------------------------------------------------

deploy_blocklist() {
    log_step "Deploying blocklist"

    local src_dir="$REPO_ROOT/etc/ark/domains/focused"
    local dst_dir="$ARK_DATA_PATH/domains/focused"

    mkdir -p "$dst_dir"

    # Deploy sources.json (create if missing, don't overwrite user changes)
    local sources_dst="$dst_dir/sources.json"
    if [[ ! -f "$sources_dst" ]]; then
        if [[ -f "$src_dir/sources.json" ]]; then
            deploy_file "$src_dir/sources.json" "$sources_dst"
            log "Seeded sources.json from repo"
        else
            log_error "sources.json missing in both repo and live"
            return 1
        fi
    else
        log "sources.json already exists — not overwriting"
    fi

    # Deploy blocklist-custom.txt (create if missing, don't overwrite)
    local custom_dst="$dst_dir/blocklist-custom.txt"
    if [[ ! -f "$custom_dst" ]]; then
        if [[ -f "$src_dir/blocklist-custom.txt" ]]; then
            deploy_file "$src_dir/blocklist-custom.txt" "$custom_dst" 640
            log "Seeded blocklist-custom.txt from repo"
        else
            printf '# Custom blocklist -- user-maintained additions\n' > "$custom_dst"
            chown root:root "$custom_dst"
            chmod 644 "$custom_dst"
            log "WARN: blocklist-custom.txt missing in both repo and live -- seeded header-only"
        fi
    else
        log "blocklist-custom.txt already exists — not overwriting"
    fi

    # Deploy blocklist-exceptions.txt (create if missing, don't overwrite)
    local exceptions_dst="$dst_dir/blocklist-exceptions.txt"
    if [[ ! -f "$exceptions_dst" ]]; then
        if [[ -f "$src_dir/blocklist-exceptions.txt" ]]; then
            deploy_file "$src_dir/blocklist-exceptions.txt" "$exceptions_dst"
            log "Seeded blocklist-exceptions.txt from repo"
        else
            printf '# Auto-managed exemptions -- unblocked from parent wildcards\n' > "$exceptions_dst"
            chown root:root "$exceptions_dst"
            chmod 644 "$exceptions_dst"
            log "WARN: blocklist-exceptions.txt missing in both repo and live -- seeded header-only"
        fi
    else
        log "blocklist-exceptions.txt already exists — not overwriting"
    fi

    # Deploy blocklist-exclude.txt (create if missing, don't overwrite)
    local exclude_dst="$dst_dir/blocklist-exclude.txt"
    if [[ ! -f "$exclude_dst" ]]; then
        if [[ -f "$src_dir/blocklist-exclude.txt" ]]; then
            deploy_file "$src_dir/blocklist-exclude.txt" "$exclude_dst"
            log "Seeded blocklist-exclude.txt from repo"
        else
            # Create empty exclude file if neither exists
            printf '# Auto-managed -- domains excluded from generation\n' > "$exclude_dst"
            chown root:root "$exclude_dst"
            chmod 644 "$exclude_dst"
            log "Created empty blocklist-exclude.txt"
        fi
    else
        log "blocklist-exclude.txt already exists — not overwriting"
    fi

    # Download all enabled sources and regenerate
    log "Running netmgr sources update..."
    if ! netmgr sources update; then
        log_error "netmgr sources update failed"
        return 1
    fi

    local output="$ARK_DATA_PATH/domains/blocklist.dnsmasq.conf"
    if [[ ! -f "$output" ]]; then
        log_error "blocklist.dnsmasq.conf not created after generate"
        log_error "Source files in domains/: $(ls "$ARK_DATA_PATH/domains/" 2>&1)"
        return 1
    fi
    log_ok "Blocklist deployed ($(wc -l < "$output") lines -> $output)"
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

    log "Validating rendered configs (nft -c -f + dnsmasq --test)..."
    if ! python3 "$ARK_DATA_PATH/scripts/netmgr.py" validate focused; then
        log_error "Config validation failed"
        return 1
    fi
    log_ok "Configs valid"
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

check_retired_paths
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
deploy_system_dns
deploy_blocklist
deploy_browser_policies
deploy_ark_perms
deploy_timeshift
validate_configs
reload_services

log_step "Lockdown complete"
exit 0
