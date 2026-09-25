#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 40-system_config.sh — sudo-needing dotfile-adjacent system config
#
# Invariant: dotfile management (make dotfiles / make dev) is sudo-free by
# design — user-scope deploys must never elevate. Any dotfile-adjacent
# config that requires root lives HERE, never in the Makefile or a
# user-phase module. Currently: the wlsunset sleep hook.
# Former residents and their owners: dark mode → user phase (redundant
# with dotfiles/sway/scripts/autostart.sh, which re-applies it every sway
# start); cask dirs → 60-ark deploy_ark_perms (+ runtime ensure_cask_dirs);
# udev → 30-hardware; polkit → 65-vm. DNS lives in netmgr
# (lib/60-ark.sh deploy_system_dns).
# set -euo pipefail handles hard failures (exit 1). Functions return 0 on
# skip (source not available) which is not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers/common.sh"

# ---------------------------------------------------------------------------
# 1. Systemd sleep hook — re-evaluate wlsunset after suspend/resume
# ---------------------------------------------------------------------------

setup_sleep_hook() {
    log_step "Systemd sleep hook"

    local src="$REPO_ROOT/system/systemd/system-sleep/wlsunset-resume.sh"
    local dst="/usr/lib/systemd/system-sleep/wlsunset-resume.sh"
    if [[ ! -f "$src" ]]; then
        log_warn "Sleep hook: source not found — skipping"
        return 0
    fi
    sudo mkdir -p /usr/lib/systemd/system-sleep
    if ! sudo cmp -s "$src" "$dst" 2>/dev/null; then
        sudo cp "$src" "$dst"
        sudo chmod 755 "$dst"
        log_ok "Sleep hook: wlsunset-resume.sh deployed"
    else
        log_ok "Sleep hook: wlsunset-resume.sh already up to date"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "System configuration"

setup_sleep_hook

log_step "System config complete"
exit 0
