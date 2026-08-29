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
    # Top-level scripts: ark.py shim, immutable_lib.py, mode.py, netmgr.py
    for f in "$REPO_ROOT"/ark/scripts/*.py; do
        deploy_file "$f" "$ARK_DATA_PATH/scripts/$(basename "$f")" 644
    done
    # opslog (from lib/python/)
    deploy_file "$REPO_ROOT/lib/python/opslog.py" "$ARK_DATA_PATH/scripts/opslog.py"
    # Sub-packages: ark/, cask/, netmgr/
    for pkg in ark cask netmgr; do
        while IFS= read -r f; do
            rel="${f#"$REPO_ROOT/ark/scripts/"}"
            mkdir -p "$ARK_DATA_PATH/scripts/$(dirname "$rel")"
            deploy_file "$f" "$ARK_DATA_PATH/scripts/$rel"
        done < <(find "$REPO_ROOT/ark/scripts/$pkg" -name '*.py' -type f | sort)
    done
    # immutable.sh wrapper + netns allowlist
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
    rm -f "$ARK_BIN_PATH/cask-mobile"
    rm -f "$ARK_BIN_PATH/lockdown"
    deploy_file "$REPO_ROOT/ark/scripts/cask/mcask.py"   "$ARK_BIN_PATH/mcask"  755
    deploy_file "$REPO_ROOT/ark/scripts/cask/uncask.py"  "$ARK_BIN_PATH/uncask" 755
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
    # Exec-grant deploy — builds thin shims + inet (dispatch grants removed).
    # Runs after subst_templates (renders wrappers.py before it's imported).
    python3 "$ARK_DATA_PATH/scripts/netmgr.py" exec-grant deploy
    log_ok "System DNS configured"
}

# ---------------------------------------------------------------------------
# 13. Browser policy templates
# ---------------------------------------------------------------------------

deploy_browser_policies() {
    log_step "Browser policies"
    # Browser policy sources staged to $ARK_DATA_PATH. Format per line:
    #   <repo-rel-path>|<staged-name>|<mode>
    # Adding/removing a browser = one line here + one entry in the BROWSERS
    # registry (ark/scripts/netmgr/policies.py).
    local browser_sources=(
        "dotfiles/browsers/brave/policy.json.template|brave-policy.json.template|640"
        "dotfiles/browsers/chrome/policy.json.template|chrome-policy.json.template|640"
    )
    local spec src staged mode
    for spec in "${browser_sources[@]}"; do
        IFS='|' read -r src staged mode <<< "$spec"
        deploy_file "$REPO_ROOT/$src" "$ARK_DATA_PATH/$staged" "$mode"
    done
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
    chmod 755 "$ARK_DATA_PATH"
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

    # Deploy focused/domain files — always from repo (repo is the source of
    # truth; live is a deploy artifact). netmgr mutations are repo-first
    # (write repo + sync live), so overwriting is a no-op when in sync. Live
    # copies are root:root — custom 640, others 644 — so non-root users can't
    # modify them. A missing repo file is a broken checkout — abort, don't
    # silently keep a stale live copy.
    local file mode
    for file in sources.json blocklist-custom.txt blocklist-exceptions.txt blocklist-exclude.txt; do
        case "$file" in
            blocklist-custom.txt) mode=640 ;;
            *) mode=644 ;;
        esac
        if [[ -f "$src_dir/$file" ]]; then
            deploy_file "$src_dir/$file" "$dst_dir/$file" "$mode"
            log "Deployed $file from repo"
        else
            log_error "$file missing in repo"
            return 1
        fi
    done

    # Download all enabled sources — download only, never auto-generate. The
    # final blocklist is curated + generated by the user (netmgr generate) so
    # ark enable's fail-closed preflight (check_blocklist_dnsmasq) stays
    # meaningful: a forgotten generate aborts enable instead of locking with an
    # uncurated list.
    #
    # Deliberately NOT idempotent: install.sh runs infrequently while upstream
    # lists (StevenBlack, blocklistproject, ...) are refreshed near-daily, so
    # re-downloading on every run guarantees the latest lists are pulled — the
    # one intentional exception to the repo's idempotent deploy model. It is
    # fail-tolerant: a failed fetch keeps the previous upstream file, and since
    # we never auto-generate, the deployed blocklist only changes when the user
    # runs `netmgr generate`.
    log "Running netmgr sources download..."
    if ! netmgr sources download; then
        log_error "netmgr sources download failed"
        return 1
    fi
    log_ok "Sources downloaded (run 'netmgr generate' to build the blocklist)"
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

    if [[ -f "$ARK_DATA_PATH/domains/focused/blocklist.dnsmasq.conf" ]]; then
        log "Validating rendered configs (nft -c -f + dnsmasq --test)..."
        if ! python3 "$ARK_DATA_PATH/scripts/netmgr.py" validate focused; then
            log_error "Config validation failed"
            return 1
        fi
    else
        log "Blocklist not generated yet — run 'netmgr generate' before ark enable"
        log "Validating base config (nft -c -f + dnsmasq --test, unrestricted)..."
        if ! python3 "$ARK_DATA_PATH/scripts/netmgr.py" validate unrestricted; then
            log_error "Config validation failed"
            return 1
        fi
    fi
    log_ok "Configs valid"
}

# ---------------------------------------------------------------------------
# 18. Reload
# ---------------------------------------------------------------------------

reload_services() {
    log_step "Reloading services"
    systemctl daemon-reload
    # Clear any stale regular-file entry in the wants dir — systemctl enable
    # only manages symlinks and fails with "File ... already exists" if a
    # leftover copy (e.g. from a manual test) sits there.
    rm -f /etc/systemd/system/multi-user.target.wants/internet-netns.service
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
deploy_ark_perms
deploy_system_dns
deploy_blocklist
deploy_browser_policies
deploy_timeshift
validate_configs
reload_services

log_step "Lockdown complete"
exit 0
