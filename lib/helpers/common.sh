#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# common.sh — Shared helpers for all install modules
#
# Sourced by each module. Provides paths, logging, and utility functions.
# 20-C: self-prime (fill-unset-only from $REPO_ROOT/config.txt), LOG_DIR
# ensure, orchestrator-marker vs standalone transcript select, shared line
# grammar (opslog.py = reference, this file = port per D12), log_run tee,
# newline-sanitize. Scrub parity is a stated limitation: never interpolate
# secrets into log calls + gitleaks backstop (bash cannot match the sink
# scrubber). Secrets arrive via lib/helpers/gopass.sh executable CLI, never via
# orchestrator env passing.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# ADR-011: require_user() refuses root at source time (see below), so a
# SUDO_USER adaptation is unreachable in every supported flow — HOME is
# always the invoking user's.
REAL_HOME="$HOME"

# ---------------------------------------------------------------------------
# Self-prime (§20.1 item 10): fill unset keys only from $REPO_ROOT/config.txt
#
# Mirrors install.py load_env(): split on first '=' only, strip one matched
# quote pair, skip blanks/comments/lines without '='. Preserves precedence
# config.txt < real env vars < gopass (gopass via CLI, never env). No .local
# layer. Never CONFIG_ENV/config.env in any form — config.txt exclusive.
# ---------------------------------------------------------------------------

_common_self_prime() {
    local cfg="$REPO_ROOT/config.txt"
    [[ -f "$cfg" ]] || return 0
    local line key raw val q
    while IFS= read -r line || [[ -n "$line" ]]; do
        # trim leading/trailing whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
        [[ "$line" != *"="* ]] && continue
        key="${line%%=*}"
        raw="${line#*=}"
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        val="${raw#"${raw%%[![:space:]]*}"}"
        val="${val%"${val##*[![:space:]]}"}"
        [[ -z "$key" ]] && continue
        if [[ "${#val}" -ge 2 ]]; then
            q="${val:0:1}"
            if [[ "$q" == '"' || "$q" == "'" ]]; then
                if [[ "${val: -1}" == "$q" ]]; then
                    val="${val:1:-1}"
                else
                    # unterminated quote: strip opener only, mirror load_env
                    # loosely (load_env requires matching pair; bash keeps
                    # the raw tail rather than failing at source time —
                    # load_env remains the strict validator under install.py)
                    val="${val:1}"
                fi
            fi
        fi
        # fill-unset-only: real env wins over config.txt
        if [[ -z "${!key:-}" ]]; then
            printf -v "$key" '%s' "$val"
            # shellcheck disable=SC2163
            # intentional dynamic export by name
            export "${key?}"
        fi
    done < "$cfg"
}

_common_self_prime

# ---------------------------------------------------------------------------
# Unified-config derivation (Phase 12-A; install.py-ready)
#
# Values computable at runtime are derived here, never stored in config.txt:
#   USER_UID, OPENCODE_PATH, TLE_FALLBACK_PATH, ARK_REPO_ETC_PATH, TERMINAL.
# Precedence: config.txt < real env vars < gopass.
# ---------------------------------------------------------------------------

# Derived identity/paths (exported for gomplate-env + child modules).
USER_UID="$(id -u)"
export USER_UID
OPENCODE_PATH="$HOME/.opencode"
export OPENCODE_PATH
TLE_FALLBACK_PATH="$HOME/go/bin/tle"
export TLE_FALLBACK_PATH
ARK_REPO_ETC_PATH="$REPO_ROOT/etc/ark"
export ARK_REPO_ETC_PATH

# TERMINAL: explicit value wins; otherwise kitty or fail loud (no fallback).
if [[ -z "${TERMINAL:-}" ]]; then
    if command -v kitty >/dev/null 2>&1; then
        TERMINAL="kitty"
        export TERMINAL
    else
        echo "ERROR: TERMINAL is unset and kitty is not installed — run: sudo apt-get install -y kitty" >&2
        return 1 2>/dev/null || exit 1
    fi
fi

# Refuse-root (ADR-011): the single enforcement point for all bash modules.
# Every module sources this file, so no per-module guard is needed (or
# wanted — it would be dead code: this fires first). EUID is 0 under
# both `su -` and a sudo prefix; SUDO_USER is cosmetic, never checked.
# (The Python mirror is lib/helpers/preconditions.py — one per runtime,
# since this must work before python3 is known to exist.)
if [[ "$EUID" -eq 0 ]]; then
    echo "  ERROR: Do not run $(basename "${BASH_SOURCE[1]:-common.sh}") as root." >&2
    echo "  Run it as your normal user (privileged steps escalate via sudo themselves)." >&2
    return 1 2>/dev/null || exit 1
fi

# USERNAME assert (A1 single-deploy target): fail loud on mismatch.
if [[ "$(whoami)" != "${USERNAME:-mike}" ]]; then
    echo "ERROR: USERNAME='${USERNAME:-}' but whoami='$(whoami)' (expected 'mike')" >&2
    return 1 2>/dev/null || exit 1
fi
LOG_DIR="$REAL_HOME/.config/install"
NEEDS_REBOOT_FILE="$LOG_DIR/.install-need-reboot"

# ---------------------------------------------------------------------------
# Transcript select (§20.2: one per run, orchestrator-marker vs standalone)
#
# Orchestrated: install.py exports INSTALL_TRANSCRIPT=<abs path> via
# child_env(); shared file, orchestrator owns latest/prune/ownership.
# Standalone: mint install.<UTC-%F-%H%M%S>-<module>.log + atomic latest.
# Location stays ~/.config/install (XDG-impure, deliberate — D11).
# Unwritable marker → fail loud (a split run proves nothing); stale env
# marker in an interactive shell → ignore and mint own with a warning.
# ---------------------------------------------------------------------------

_common_utc_ts() {
    date -u '+%Y-%m-%d-%H%M%S'
}

_common_caller_label() {
    local src=""
    if [[ "${#BASH_SOURCE[@]}" -gt 1 ]]; then
        src="${BASH_SOURCE[1]}"
    else
        src="${BASH_SOURCE[0]}"
    fi
    src="$(basename "$src")"
    src="${src%.*}"
    if [[ -z "$src" || "$src" == "common" ]]; then
        printf 'standalone'
    else
        printf '%s' "$src"
    fi
}

mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR" 2>/dev/null || true

if [[ -n "${INSTALL_TRANSCRIPT:-}" ]]; then
    _marker_dir="$(dirname "$INSTALL_TRANSCRIPT")"
    if [[ -f "$INSTALL_TRANSCRIPT" && -w "$INSTALL_TRANSCRIPT" ]] || \
       [[ ! -e "$INSTALL_TRANSCRIPT" && -d "$_marker_dir" && -w "$_marker_dir" ]]; then
        LOG_FILE="$INSTALL_TRANSCRIPT"
    else
        echo "ERROR: INSTALL_TRANSCRIPT='$INSTALL_TRANSCRIPT' not writable — refusing to split the run" >&2
        return 1 2>/dev/null || exit 1
    fi
    unset _marker_dir
else
    _standalone_label="$(_common_caller_label)"
    _standalone_ts="$(_common_utc_ts)"
    LOG_FILE="$LOG_DIR/install.${_standalone_ts}-${_standalone_label}.log"
    if [[ -e "$LOG_FILE" ]]; then
        LOG_FILE="$LOG_DIR/install.${_standalone_ts}-${_standalone_label}-$$.log"
    fi
    : > "$LOG_FILE" || {
        echo "ERROR: cannot write transcript: $LOG_FILE" >&2
        return 1 2>/dev/null || exit 1
    }
    chmod 644 "$LOG_FILE" 2>/dev/null || true
    # Standalone owns latest (orchestrated runs: install.py owns it, do not touch).
    _latest_tmp="$LOG_DIR/.latest.$$.tmp"
    rm -f "$_latest_tmp" 2>/dev/null || true
    if ln -s "$(basename "$LOG_FILE")" "$_latest_tmp" 2>/dev/null; then
        mv -f "$_latest_tmp" "$LOG_DIR/latest" 2>/dev/null || rm -f "$_latest_tmp" 2>/dev/null || true
    fi
    unset _standalone_label _standalone_ts _latest_tmp
fi
export LOG_FILE LOG_DIR

# ---------------------------------------------------------------------------
# Curl timeout defaults (seconds)
# ---------------------------------------------------------------------------

CURL_TIMEOUT_CONNECT=10
CURL_TIMEOUT_API=30
CURL_TIMEOUT_DOWNLOAD=240
CURL_TIMEOUT_INSTALL=180

# ---------------------------------------------------------------------------
# Color helpers (TTY-only color; file never ANSI)
# ---------------------------------------------------------------------------

if [[ -t 2 ]] && command -v tput >/dev/null 2>&1 && tput sgr0 >/dev/null 2>&1; then
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
# Logging — shared line grammar (opslog.py = reference, this = port, D12)
#   file: YYYY-MM-DD HH:MM:SS UTC LEVEL component(step) msg (never ANSI)
#   terminal: curated, no timestamps, TTY-only color
#   RAW markers byte-identical: === START ... === / === END ... (...) ===
#   floors: log_* detail file-only; log_ok/log_step visible; warn/error both
#   durability: >> + per-record write (accepted crash window = power loss)
# ---------------------------------------------------------------------------

INSTALL_COMPONENT="${INSTALL_COMPONENT:-install}"
INSTALL_STEP="${INSTALL_STEP:-$(_common_caller_label)}"
export INSTALL_COMPONENT INSTALL_STEP

# newline-sanitize every logged message (log-injection defense): CR/LF and
# other control chars become spaces before the line hits file or terminal.
_sanitize() {
    local s="$1"
    s="${s//$'\n'/ }"
    s="${s//$'\r'/ }"
    # strip remaining ASCII control chars (keep tab as space)
    s="$(printf '%s' "$s" | tr '\000-\010\013-\037\177' ' ')"
    printf '%s' "$s"
}

_utc_now() {
    date -u '+%Y-%m-%d %H:%M:%S'
}

_file_line() {
    local level="$1"; shift
    local msg
    msg="$(_sanitize "$*")"
    printf '%s UTC %-5s %s(%s) %s\n' "$(_utc_now)" "$level" "$INSTALL_COMPONENT" "$INSTALL_STEP" "$msg" >> "$LOG_FILE"
}

log() {
    _file_line "INFO" "$*"
}

log_ok() {
    local msg
    msg="$(_sanitize "$*")"
    echo "  ${C_GREEN}OK   ${C_RESET} $msg" >&2
    printf '%s UTC %-5s %s(%s) %s\n' "$(_utc_now)" "OK" "$INSTALL_COMPONENT" "$INSTALL_STEP" "$msg" >> "$LOG_FILE"
}

log_warn() {
    local msg
    msg="$(_sanitize "$*")"
    echo "  ${C_YELLOW}WARN ${C_RESET} $msg" >&2
    printf '%s UTC %-5s %s(%s) %s\n' "$(_utc_now)" "WARN" "$INSTALL_COMPONENT" "$INSTALL_STEP" "$msg" >> "$LOG_FILE"
}

log_error() {
    local msg
    msg="$(_sanitize "$*")"
    echo "  ${C_RED}ERROR${C_RESET} $msg" >&2
    printf '%s UTC %-5s %s(%s) %s\n' "$(_utc_now)" "ERROR" "$INSTALL_COMPONENT" "$INSTALL_STEP" "$msg" >> "$LOG_FILE"
}

log_step() {
    local msg
    msg="$(_sanitize "$*")"
    echo "${C_BOLD}>>>${C_RESET} $msg" >&2
    printf '%s UTC %-5s %s(%s) %s\n' "$(_utc_now)" "STEP" "$INSTALL_COMPONENT" "$INSTALL_STEP" "$msg" >> "$LOG_FILE"
}

# RAW verbatim line (byte-identical markers); goes to file + terminal as-is.
log_raw() {
    printf '%s\n' "$1" >> "$LOG_FILE"
    printf '%s\n' "$1" >&2
}

log_session_start() {
    log_raw "=== START $1 ==="
}

log_session_end() {
    # === END <label> (rc=N,T=Ns) === — byte-stable with opslog.end_module
    log_raw "=== END $1 (rc=$2,T=$3s) ==="
}

# log_run CMD... — pure-bash tee for noisy apt/curl calls. Full output to
# the file (detail), terminal stays milestone-quiet; returns the child's rc.
# Stops >/dev/null-ing evidence: callers pipe noisy commands through here.
log_run() {
    local rc line
    local out
    set +e
    out="$("$@" 2>&1)"
    rc=$?
    set -e
    while IFS= read -r line || [[ -n "$line" ]]; do
        log "$line"
    done <<< "$out"
    return "$rc"
}

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

# is_vm — exit 0 if running in a VM (systemd-detect-virt: 0 = virtualized).
# Canonical systemd detection: CPUID hypervisor bit + DMI vendor + sysfs.
is_vm() {
    systemd-detect-virt -q 2>/dev/null
}

pkg_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

pip_installed() {
    python3 -c "import $1" 2>/dev/null
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
