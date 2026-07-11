#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 00-checks.sh — Pre-flight checks
#
# Validates environment before any install steps run.
# Exit 0 = all checks passed, exit 1 = abort
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RC=0

# ---------------------------------------------------------------------------
# 1. Refuse root
# ---------------------------------------------------------------------------

if [[ $EUID -eq 0 ]]; then
    log_error "This script must NOT be run as root."
    log "  Run it as a regular user with sudo privileges: ./install.sh"
    RC=1
fi

# ---------------------------------------------------------------------------
# 2. Sudo group
# ---------------------------------------------------------------------------

if ! groups | grep -q '\bsudo\b'; then
    log_error "User '$USER' is not in the sudo group."
    log "  Run: sudo usermod -aG sudo $USER"
    log "  Then log out and back in."
    RC=1
fi

# ---------------------------------------------------------------------------
# 3. DNS resolution
# ---------------------------------------------------------------------------

if ! timeout 5 getent hosts raw.githubusercontent.com &>/dev/null; then
    log_error "DNS resolution failed (cannot resolve raw.githubusercontent.com)."
    log "  Check /etc/resolv.conf and network connectivity."
    RC=1
else
    log_ok "DNS resolution"
fi

# ---------------------------------------------------------------------------
# 4. TCP connectivity
# ---------------------------------------------------------------------------

if ! timeout 5 bash -c 'echo > /dev/tcp/raw.githubusercontent.com/443' 2>/dev/null; then
    log_error "No internet connectivity (cannot reach raw.githubusercontent.com:443)."
    log "  Check your network connection."
    RC=1
else
    log_ok "TCP connectivity"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

if (( RC == 0 )); then
    log_ok "Pre-flight checks passed"
else
    log_error "Pre-flight checks failed — aborting"
fi

exit $RC
