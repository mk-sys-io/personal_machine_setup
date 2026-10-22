#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 57-root-account.sh — Unlock the root account for su - recovery
#
# Called from install.py only. Position: after 55-security, before
#   60-ark-deploy.
#
# Why: Debian d-i with a blank root password writes `root:!:...` into
#   /etc/shadow — the '!' prefix LOCKS the account (man 5 shadow). `su -`
#   always prompts, but PAM rejects every string (blank, sudo password,
#   anything), so there is no console/single-user root entry. This step
#   unlocks root by setting it to the committed DISABLE_ROOT_PASSWORD, the
#   same value ark disable resets it to.
#
# Mechanism: chpasswd -c YESCRYPT (bypasses pam_unix obscure, which would
#   reject a trivial password), mirroring ark/scripts/ark/disable.py. The
#   password travels on stdin, never argv, and is never logged. Fallback:
#   openssl passwd -6 + usermod -p (openssl shipped by 20-packages).
#
# Guard: if root already has a usable password (e.g. an Ark-casked random
#   hash, or an operator-set one) it is NOT clobbered.
#
# Exit contract (0=pass, 1=fail, 2=skip):
#   0  root password set now and verified in /etc/shadow
#   1  DISABLE_ROOT_PASSWORD empty; chpasswd + fallback both failed;
#      verification failed
#   2  root already has a usable password — left untouched
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers/common.sh"

# Belt-and-braces for standalone runs (the orchestrator creates this too).
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# 0. Preconditions
# ---------------------------------------------------------------------------

if [[ -z "${DISABLE_ROOT_PASSWORD:-}" ]]; then
    log_error "DISABLE_ROOT_PASSWORD is empty — set it in committed config.txt"
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Current state — usable hash means the desired account state already holds
# ---------------------------------------------------------------------------

shadow_hash() {
    sudo getent shadow root | cut -d: -f2
}

before="$(shadow_hash)"
if [[ -n "$before" && "$before" != "!"* && "$before" != "*"* ]]; then
    log_warn "root already has a usable password — not clobbered"
    exit 2
fi

# ---------------------------------------------------------------------------
# 2. Set the password
# ---------------------------------------------------------------------------

if ! printf 'root:%s\n' "$DISABLE_ROOT_PASSWORD" | sudo chpasswd -c YESCRYPT; then
    if ! cmd_exists openssl; then
        log_error "chpasswd -c YESCRYPT failed and openssl is absent for the fallback"
        exit 1
    fi
    hash="$(openssl passwd -6 "$DISABLE_ROOT_PASSWORD")"
    if [[ -z "$hash" ]]; then
        log_error "openssl produced an empty hash"
        exit 1
    fi
    sudo usermod -p "$hash" root || { log_error "usermod -p fallback failed"; exit 1; }
fi

# ---------------------------------------------------------------------------
# 3. Verify — hash changed and is no longer locked
# ---------------------------------------------------------------------------

after="$(shadow_hash)"
if [[ -z "$after" || "$after" == "$before" || "$after" == "!"* || "$after" == "*"* ]]; then
    log_error "root password did not verify in /etc/shadow (locked or unchanged)"
    exit 1
fi

log_ok "root account unlocked — su - recovery enabled"
exit 0
