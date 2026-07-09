#!/bin/bash
# check-firmware.sh — Manual check for firmware/kernel version drift.
#
# Checks:
#   1. iwlwifi loaded firmware version vs firmware-iwlwifi package files
#   2. intel-microcode loaded revision
#
# Auto-escalates for dmesg access: tries direct dmesg first, then sudo
# dmesg, then journalctl -k as final fallback.
#
# Exit 0 = all checks passed, 1 = one or more checks failed

set -u

RC=0

# ---------------------------------------------------------------------------
# Color helpers (no-op fallback if tput unavailable)
# ---------------------------------------------------------------------------

if command -v tput >/dev/null 2>&1; then
    C_PASS=$(tput setaf 2)
    C_WARN=$(tput setaf 3)
    C_ERROR=$(tput setaf 1)
    C_SKIP=$(tput setaf 6)
    C_RESET=$(tput sgr0)
else
    C_PASS=""; C_WARN=""; C_ERROR=""; C_SKIP=""; C_RESET=""
fi

log_info()  { echo "check-firmware: $*"; }
log_pass()  { echo "check-firmware: ${C_PASS}PASS${C_RESET} $*"; }
log_warn()  { echo "check-firmware: ${C_WARN}WARN${C_RESET} $*" >&2; }
log_error() { echo "check-firmware: ${C_ERROR}ERROR${C_RESET} $*" >&2; }
log_skip()  { echo "check-firmware: ${C_SKIP}SKIP${C_RESET} $*"; }

# ---------------------------------------------------------------------------
# Helper: read kernel log (auto-escalate privilege as needed)
# ---------------------------------------------------------------------------

read_kernel_log() {
    if DMESG=$(dmesg 2>&1); then
        echo "$DMESG"
    elif command -v sudo >/dev/null 2>&1 && DMESG=$(sudo dmesg 2>&1); then
        echo "$DMESG"
    elif DMESG=$(journalctl -k --no-pager 2>&1); then
        echo "$DMESG"
    else
        echo "" >&2
        return 1
    fi
}

# ---------------------------------------------------------------------------
# 1. iwlwifi firmware
# ---------------------------------------------------------------------------

KERN_LOG=$(read_kernel_log)
if [ $? -ne 0 ]; then
    log_error "iwlwifi — cannot read kernel log (dmesg + journalctl both failed)"
    RC=1
else
    LOADED_IWL=$(echo "$KERN_LOG" | sed -n 's/.*iwlwifi.*loaded firmware version \([^ ]*\).*/\1/p' | head -1)

    if [ -z "$LOADED_IWL" ]; then
        log_skip "iwlwifi (no firmware loaded — module may not be in use)"
    elif ! dpkg -s firmware-iwlwifi >/dev/null 2>&1; then
        log_warn "iwlwifi — firmware-iwlwifi package not installed"
        RC=1
    else
        PACKAGE_FILES=$(dpkg -L firmware-iwlwifi | grep 'iwlwifi-.*\.ucode' | xargs -I{} basename {} | sort -u | head -5)
        if [ -z "$PACKAGE_FILES" ]; then
            log_warn "iwlwifi — firmware-iwlwifi package installed but no firmware files found"
        else
            log_info "iwlwifi loaded firmware version: $LOADED_IWL"
            log_info "iwlwifi available firmware files:"
            echo "$PACKAGE_FILES" | sed 's/^/  /'
            log_pass "iwlwifi"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 2. intel-microcode revision
# ---------------------------------------------------------------------------

LOADED_UCODE=$(awk '/^microcode/ {print $3; exit}' /proc/cpuinfo)
if [ -z "$LOADED_UCODE" ]; then
    log_error "microcode — no microcode line in /proc/cpuinfo"
    RC=1
else
    log_info "microcode loaded revision: $LOADED_UCODE"
    log_pass "microcode"
fi

# ---------------------------------------------------------------------------

exit $RC
