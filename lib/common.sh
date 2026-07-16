#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# common.sh — Shared helpers for all install modules
#
# Sourced by each module. Provides paths, logging, and utility functions.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME="$HOME"
fi
LOG_DIR="$REAL_HOME/.config/install"
LOG_FILE="$LOG_DIR/install.$(date +%F).log"
NEEDS_REBOOT_FILE="$LOG_DIR/.install-need-reboot"

# ---------------------------------------------------------------------------
# Curl timeout defaults (seconds)
# ---------------------------------------------------------------------------

CURL_TIMEOUT_CONNECT=10
CURL_TIMEOUT_API=30
CURL_TIMEOUT_DOWNLOAD=120
CURL_TIMEOUT_INSTALL=180

# ---------------------------------------------------------------------------
# Color helpers (no-op fallback if tput unavailable)
# ---------------------------------------------------------------------------

if command -v tput >/dev/null 2>&1 && tput sgr0 >/dev/null 2>&1; then
    C_RED=$(tput setaf 1)
    C_GREEN=$(tput setaf 2)
    C_YELLOW=$(tput setaf 3)
    C_CYAN=$(tput setaf 6)
    C_BOLD=$(tput bold)
    C_RESET=$(tput sgr0)
else
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_CYAN=""
    C_BOLD=""
    C_RESET=""
fi

# ---------------------------------------------------------------------------
# Logging — each function writes directly to its destination(s).
#   log / log_ok   → log file only (detail messages)
#   log_step       → terminal + log file (milestones)
#   log_warn/error → terminal + log file (problems)
# ---------------------------------------------------------------------------

log() {
    echo "$(date '+%H:%M:%S') $*" >> "$LOG_FILE"
}

log_ok() {
    echo "$(date '+%H:%M:%S') OK $*" >> "$LOG_FILE"
}

log_warn() {
    echo "$(date '+%H:%M:%S') ${C_YELLOW}WARN${C_RESET} $*" >&2
    echo "$(date '+%H:%M:%S') WARN $*" >> "$LOG_FILE"
}

log_error() {
    echo "$(date '+%H:%M:%S') ${C_RED}ERROR${C_RESET} $*" >&2
    echo "$(date '+%H:%M:%S') ERROR $*" >> "$LOG_FILE"
}

log_step() {
    echo "${C_CYAN}>>>${C_RESET} $*" >&2
    echo ">>> $*" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

pkg_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

needs_reboot() {
    mkdir -p "$(dirname "$NEEDS_REBOOT_FILE")"
    cat /proc/sys/kernel/random/boot_id > "$NEEDS_REBOOT_FILE"
}

# Returns 0 (true) if any reboot signal is active:
#   - NEEDS_REBOOT_FILE (explicit marker from modules like 35-nvidia.sh)
#   - /run/reboot-required (system-level: kernel updates, security patches)
reboot_needed() {
    [[ -f "$NEEDS_REBOOT_FILE" ]] || [[ -f /run/reboot-required ]]
}

# retry N CMD...
# Tries CMD up to N times with 3s delay between attempts.
retry() {
    local attempts=$1
    shift
    local delay=3
    local attempt=1

    while (( attempt <= attempts )); do
        if "$@"; then
            return 0
        fi
        if (( attempt < attempts )); then
            log_warn "Attempt $attempt/$attempts failed, retrying in ${delay}s..."
            sleep "$delay"
        fi
        (( attempt++ ))
    done

    log_error "All $attempts attempts failed for: $*"
    return 1
}
