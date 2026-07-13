#!/usr/bin/env bash
# ===========================================================================
# install.sh — Modular orchestrator for linux_setup
#
# Runs independent install modules in order, captures exit codes,
# prints a summary, and prompts for reboot if needed.
#
# IMPORTANT: set -e and set -u are intentionally omitted.
# - No set -e: The orchestrator runs module scripts directly as subprocesses to
#   capture exit codes (0=pass, 1=fail, 2=skip, 3=partial). set -e would
#   abort on any non-zero exit before we can record the result. Each module
#   uses set -euo pipefail internally for fail-fast behavior.
# - No set -u: The orchestrator is ~200 lines with simple flow. Typos in
#   unset variables are low risk here. Modules enforce strict mode.
# - set -a IS used: All config.env and github.env variables are exported
#   to child processes (modules, make). Internal orchestrator vars leaking
#   is benign.
# ===========================================================================

set -a

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

# ---------------------------------------------------------------------------
# Reboot marker — clear stale markers (from previous boot), warn if still pending
# ---------------------------------------------------------------------------

if [[ -f "$NEEDS_REBOOT_FILE" ]]; then
    saved_boot_id=$(cat "$NEEDS_REBOOT_FILE")
    current_boot_id=$(cat /proc/sys/kernel/random/boot_id)
    if [[ "$saved_boot_id" == "$current_boot_id" ]]; then
        log_warn "Previous reboot still pending (from this boot)"
    else
        log "Clearing stale reboot marker (system was already rebooted)"
        rm -f "$NEEDS_REBOOT_FILE"
    fi
fi

# ---------------------------------------------------------------------------
# config.env validation
# ---------------------------------------------------------------------------

CONFIG_ENV="$REPO_ROOT/config.env"

if [[ ! -f "$CONFIG_ENV" ]]; then
    log_error "config.env not found."
    log "  Generate it: tools/bootstrap-config.sh"
    exit 1
fi

while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    if [[ -z "${value// /}" ]]; then
        log_error "config.env: $key is empty."
        log "  Edit config.env or run: tools/bootstrap-config.sh"
        exit 1
    fi
done < "$CONFIG_ENV"

source "$CONFIG_ENV"

log_ok "config.env validated"

# ---------------------------------------------------------------------------
# dev/github.env validation
# ---------------------------------------------------------------------------

GITHUB_ENV="$REPO_ROOT/dev/github.env"

if [[ ! -f "$GITHUB_ENV" ]]; then
    log_error "dev/github.env not found."
    log "  Create it: cp dev/github.env.template dev/github.env"
    exit 1
fi

source "$GITHUB_ENV"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    log_warn "GITHUB_TOKEN is empty — API calls will be unauthenticated (60 req/h limit)"
fi
if [[ -z "${GIT_USER_NAME:-}" ]]; then
    log_warn "GIT_USER_NAME is empty — git commits will have no author name"
fi
if [[ -z "${GIT_USER_EMAIL:-}" ]]; then
    log_warn "GIT_USER_EMAIL is empty — git commits will have no author email"
fi

log_ok "dev/github.env validated"

# ---------------------------------------------------------------------------
# Sudo acquisition + keepalive
# ---------------------------------------------------------------------------

sudo -v
KEEPALIVE_PID=""
trap 'kill $KEEPALIVE_PID 2>/dev/null; sudo -k' EXIT INT TERM
while true; do sudo -nv 2>/dev/null || true; sleep 60; done &
KEEPALIVE_PID=$!

# ---------------------------------------------------------------------------
# Log file initialization
# ---------------------------------------------------------------------------

mkdir -p "$LOG_DIR"
if [[ -f "$LOG_FILE" ]]; then
    printf '\n--- Re-run: %s ---\n\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
fi
log "=== Install started ===" >> "$LOG_FILE"

# ---------------------------------------------------------------------------
# Colors for summary (orchestrator doesn't use common.sh colors for modules)
# ---------------------------------------------------------------------------

if command -v tput >/dev/null 2>&1 && tput sgr0 >/dev/null 2>&1; then
    C_RED=$(tput setaf 1)
    C_GREEN=$(tput setaf 2)
    C_YELLOW=$(tput setaf 3)
    C_CYAN=$(tput setaf 6)
    C_BOLD=$(tput bold)
    C_RESET=$(tput sgr0)
else
    C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_BOLD=""; C_RESET=""
fi

# ---------------------------------------------------------------------------
# step() — Run a module, capture exit code, print summary line
# ---------------------------------------------------------------------------

RESULTS=()
LABELS=()

step() {
    local label="$1"
    shift
    LABELS+=("$label")
    echo "  $label"
    if [[ ! -f "$1" ]] && ! cmd_exists "$1"; then
        echo "    ${C_RED}FAIL${C_RESET} (missing: $1)"
        RESULTS+=("fail")
        return
    fi
    if [[ -f "$1" ]]; then
        bash "$@" >> "$LOG_FILE"
    else
        "$@" >> "$LOG_FILE"
    fi
    local rc=$?
    if (( rc == 0 )); then
        echo "    ${C_GREEN}OK${C_RESET}"
        RESULTS+=("pass")
    else
        case $rc in
            2) echo "    ${C_CYAN}SKIP${C_RESET}"; RESULTS+=("skip") ;;
            3) echo "    ${C_YELLOW}PARTIAL${C_RESET}"; RESULTS+=("partial") ;;
            *) echo "    ${C_RED}FAIL${C_RESET}"; RESULTS+=("fail") ;;
        esac
    fi
}

# ---------------------------------------------------------------------------
# Run modules in explicit order
# ---------------------------------------------------------------------------

MODULES=(
    "lib/00-checks.sh"
    "lib/20-packages.sh"
    "lib/30-hardware.sh"
    "lib/40-system_config.sh"
    "lib/50-github_setup.sh"
)

for mod in "${MODULES[@]}"; do
    step "$(basename "$mod" .sh)" "$REPO_ROOT/$mod"
done

# ---------------------------------------------------------------------------
# Make prerequisite check
# ---------------------------------------------------------------------------

if ! command -v make >/dev/null 2>&1; then
    log_error "'make' is not installed."
    log "  Install it: sudo apt-get install -y make"
    exit 1
fi

# ---------------------------------------------------------------------------
# Dotfiles + dev configs (via Makefile)
# ---------------------------------------------------------------------------

step "make-all" make -C "$REPO_ROOT" all

# ---------------------------------------------------------------------------
# System lockdown (via lib/60-lockdown.sh)
# ---------------------------------------------------------------------------

step "lockdown" sudo bash "$REPO_ROOT/lib/60-lockdown.sh"

# ---------------------------------------------------------------------------
# Log file reference
# ---------------------------------------------------------------------------

echo ""
echo "  Full log: $LOG_FILE"
echo ""

# ---------------------------------------------------------------------------
# Manual steps reminder
# ---------------------------------------------------------------------------

echo "  Post-install manual steps: glow manual_work.md"
echo ""

# ---------------------------------------------------------------------------
# Reboot prompt
# ---------------------------------------------------------------------------

if [[ -f "$NEEDS_REBOOT_FILE" ]]; then
    echo "  ${C_YELLOW}Reboot required:${C_RESET} nouveau GSP kernel param added"
    if [[ -t 0 ]]; then
        read -r -p "  Reboot now? (y/N): " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            sudo systemctl reboot
        else
            echo "  Remember to reboot later for changes to take effect."
        fi
    else
        echo "  Reboot required but running non-interactively — reboot manually."
    fi
    rm -f "$NEEDS_REBOOT_FILE"
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

sudo -k
