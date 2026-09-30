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
# 2. GNOME Keyring — Option C (blank Default) no-secrets policy
#
# Policy hinges on the assumption that the operator stores ZERO secrets here:
# browser password managers disabled (Bitwarden/gopass external), no Zed
# login/AI tokens. The vault then holds only throwaway Chromium Safe Storage
# keys, so a blank-password Default keyring (silent-but-plaintext) is a
# deliberate trade, not a vulnerability (decided 2026-09-30; Zed via oo7
# prompts even when logged out, zed#17460).
#
# Consequences: this function intentionally wires NO PAM. Silence comes from
# the blank Default (set once per machine: delete Default.keyring, next
# prompt -> leave empty), not from auto-unlock. Single daemon owner is the
# systemd socket units; do NOT add a manual `gnome-keyring-daemon --start`
# in autostart.sh (races: already initialized).
#
# REVISIT TRIGGER: the day you save a browser password OR sign into Zed
# (Collab/AI/providers) -> abandon C: create `login` keyring (password ==
# login password) via seahorse, set as default, migrate items off Default,
# backup + delete Default.keyring, then wire manual lines in /etc/pam.d/login:
#   auth optional pam_gnome_keyring.so
#   session optional pam_gnome_keyring.so auto_start
# (pam-auth-update is a no-op for unlock on trixie: its gnome-keyring config
# ships only a Password-Type stanza.) Absent `login.keyring` below is the
# EXPECTED state under C, not a warning.
# ---------------------------------------------------------------------------

setup_gnome_keyring() {
    log_step "GNOME Keyring (Option C: no-secrets policy)"

    if ! pkg_installed gnome-keyring; then
        log_warn "gnome-keyring: package not installed — skipping"
        return 0
    fi

    log_ok "gnome-keyring: SKIP PAM by policy (blank Default covers all libsecret clients; revisit on first stored secret)"
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "System configuration"

setup_sleep_hook
setup_gnome_keyring

log_step "System config complete"
exit 0
