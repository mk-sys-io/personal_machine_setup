#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 62-refindplus.sh — RefindPlus EFI boot manager (active fork of rEFInd)
#
# Downloads are handled by 20-packages (packages/github_tarball.txt); this
# module lays out the ESP, copies assets, and creates the NVRAM entry.
# Non-critical: missing inputs or VMs cause a skip (exit 2), not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers/common.sh"

VERSION="0.14.2.AF"
EXTRACTED="$HOME/.local/share/refindplus/x64-RefindPlus_001402-AF"
ESP="/boot/efi"
REFIND_DIR="$ESP/EFI/refind"
MARKER="$REFIND_DIR/.refindplus-version"
REPO_TEMPLATE="$REPO_ROOT/system/refind/refind.conf"

log_step "RefindPlus boot manager"

# VM skip
if is_vm; then
    log "SKIP: running inside a VM — RefindPlus not applicable"
    exit 2
fi

# Prereqs (non-fatal)
if ! sudo test -d "$ESP/EFI"; then
    log_warn "ESP not mounted at $ESP — skipping RefindPlus"
    exit 2
fi
if ! command -v efibootmgr >/dev/null 2>&1; then
    log_warn "efibootmgr not found — skipping RefindPlus"
    exit 2
fi
if [[ ! -f "$EXTRACTED/x64_RefindPlus_REL.efi" ]]; then
    log_warn "RefindPlus extracted binary not found at $EXTRACTED — skipping"
    exit 2
fi

# Idempotency
if sudo test -f "$MARKER" && [[ "$(sudo cat "$MARKER" 2>/dev/null || true)" == "$VERSION" ]] && sudo test -f "$REFIND_DIR/refind_x64.efi"; then
    log_ok "RefindPlus $VERSION already installed"
    exit 0
fi

sudo mkdir -p "$REFIND_DIR/icons"

# Binary
sudo cp "$EXTRACTED/x64_RefindPlus_REL.efi" "$REFIND_DIR/refind_x64.efi"
sudo chmod 755 "$REFIND_DIR/refind_x64.efi"
log_ok "RefindPlus binary installed"

# Icons from release
sudo cp "$EXTRACTED"/OtherMisc/icons/* "$REFIND_DIR/icons/" 2>/dev/null || true
log_ok "RefindPlus icons installed"

# Config (preserve existing; sample otherwise)
if ! sudo test -f "$REFIND_DIR/refind.conf"; then
    if [[ -f "$REPO_TEMPLATE" ]]; then
        sudo cp "$REPO_TEMPLATE" "$REFIND_DIR/refind.conf"
    else
        sudo cp "$EXTRACTED/config.conf" "$REFIND_DIR/refind.conf"
    fi
    log_ok "RefindPlus config installed"
else
    if [[ -f "$REPO_TEMPLATE" ]]; then
        sudo cp "$REPO_TEMPLATE" "$REFIND_DIR/refind.conf.sample"
    else
        sudo cp "$EXTRACTED/config.conf" "$REFIND_DIR/refind.conf.sample"
    fi
    log_ok "RefindPlus config exists; sample written"
fi

# Repo overrides (optional)
if [[ -d "$REPO_ROOT/system/refind/themes" ]]; then
    sudo mkdir -p "$REFIND_DIR/themes"
    sudo cp -r "$REPO_ROOT"/system/refind/themes/* "$REFIND_DIR/themes/" 2>/dev/null || true
fi
if [[ -d "$REPO_ROOT/system/refind/icons" ]]; then
    sudo cp "$REPO_ROOT"/system/refind/icons/* "$REFIND_DIR/icons/" 2>/dev/null || true
fi

# NVRAM entry
if ! sudo efibootmgr 2>/dev/null | grep -qi "RefindPlus"; then
    esp_dev="$(findmnt -no SOURCE "$ESP" 2>/dev/null || true)"
    disk=""
    part=""
    if [[ -n "$esp_dev" ]]; then
        disk="$(lsblk -no PKNAME "$esp_dev" 2>/dev/null | head -1 || true)"
        part="$(lsblk -no PARTN "$esp_dev" 2>/dev/null | head -1 || true)"
    fi
    if [[ -z "$disk" || -z "$part" ]]; then
        log_warn "Could not determine ESP disk/partition — skipping NVRAM entry"
    else
        sudo efibootmgr --create --disk "/dev/$disk" --part "$part" \
            --loader /EFI/refind/refind_x64.efi --label "RefindPlus Boot Manager" >/dev/null
        log_ok "RefindPlus NVRAM entry created"
    fi
else
    log_ok "RefindPlus NVRAM entry already present"
fi

# Secure Boot warning
if command -v mokutil >/dev/null 2>&1; then
    if mokutil --sb-state 2>/dev/null | grep -qi "SecureBoot enabled"; then
        log_warn "Secure Boot appears enabled — RefindPlus binary may not be signed"
    fi
fi

echo "$VERSION" | sudo tee "$MARKER" >/dev/null
log_ok "RefindPlus $VERSION installed"
exit 0
