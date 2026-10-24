#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 62-refindplus.sh — RefindPlus EFI boot manager (active fork of rEFInd)
#
# Downloads are handled by 20-packages (packages/github_tarball.txt); this
# module lays out the ESP, copies assets, and MAINTAINS the NVRAM entry on
# every run (not gated by the version marker):
#   - recreated when its device path no longer matches the live ESP
#     partition (stale GUID after a reinstall, clone, or re-partition —
#     NVRAM entries are write-once snapshots of a mutable layout);
#   - re-asserted first in BootOrder whenever another installer
#     (grub-install, Windows setup) prepended itself over it;
#   - deploys a boot-time self-heal: oneshot systemd unit + apt DPkg hook,
#     both running system/systemd/system/refindplus-assert-bootorder.sh
#     (firmware reorder + grub/shim upgrade coups are healed outside
#     install.py runs).
# Non-critical: missing inputs or VMs cause a skip (exit 2), not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers/common.sh"

VERSION="0.14.2.AF"
EXTRACTED="$HOME/.local/share/refindplus/x64-RefindPlus_001402-AF"
ESP="/boot/efi"
REFIND_DIR="$ESP/EFI/refind"
MARKER="$REFIND_DIR/.refindplus-version"
LABEL="RefindPlus Boot Manager"
LOADER="/EFI/refind/refind_x64.efi"

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
if ! cmd_exists efibootmgr; then
    log_warn "efibootmgr not found — skipping RefindPlus"
    exit 2
fi
if [[ ! -f "$EXTRACTED/x64_RefindPlus_REL.efi" ]]; then
    log_warn "RefindPlus extracted binary not found at $EXTRACTED — skipping"
    exit 2
fi

# --- NVRAM health check (runs on EVERY invocation, never marker-gated) -----

EFI_STATE=""
ENTRY_NUM=""
ENTRY_GUID=""

refresh_efi_state() {
    EFI_STATE="$(sudo efibootmgr -v 2>/dev/null || true)"
}

# locate_entry — set ENTRY_NUM / ENTRY_GUID from $EFI_STATE; both empty when
# no entry carries the RefindPlus label.
locate_entry() {
    ENTRY_NUM=""
    ENTRY_GUID=""
    local line
    line="$(grep -iF "$LABEL" <<<"$EFI_STATE" | head -1 || true)"
    [[ -z "$line" ]] && return 0
    ENTRY_NUM="$(sed -n 's/^Boot\([0-9a-fA-F]\{4\}\)[ *].*/\1/p' <<<"$line")"
    ENTRY_GUID="$(sed -n 's/.*HD([0-9]*,GPT,\([0-9a-fA-F-]*\),.*/\1/p' <<<"$line")"
}

resolv_esp() {
    # sets ESP_DEV, DISK, PART, LIVE_GUID; empty DISK/PART/LIVE_GUID on failure
    ESP_DEV=$(findmnt -no SOURCE "$ESP" 2>/dev/null || true)
    DISK=""
    PART=""
    LIVE_GUID=""
    [[ -n "$ESP_DEV" ]] || return 0
    DISK="$(lsblk -no PKNAME "$ESP_DEV" 2>/dev/null | head -1 || true)"
    PART="$(lsblk -no PARTN "$ESP_DEV" 2>/dev/null | head -1 || true)"
    LIVE_GUID="$(lsblk -no PARTUUID "$ESP_DEV" 2>/dev/null | head -1 || true)"
}

create_entry() {
    sudo efibootmgr --create --disk "/dev/$DISK" --part "$PART" \
        --loader "$LOADER" --label "$LABEL" >/dev/null
}



# Files first (entry must point at a binary that is already in place),
# but the version marker only gates the FILE work — NVRAM follows below.

if sudo test -f "$MARKER" && [[ "$(sudo cat "$MARKER" 2>/dev/null || true)" == "$VERSION" ]] && sudo test -f "$REFIND_DIR/refind_x64.efi"; then
    log_ok "RefindPlus $VERSION already installed"
else
    sudo mkdir -p "$REFIND_DIR/icons"

    # Binary
    sudo cp "$EXTRACTED/x64_RefindPlus_REL.efi" "$REFIND_DIR/refind_x64.efi"
    sudo chmod 755 "$REFIND_DIR/refind_x64.efi"
    log_ok "RefindPlus binary installed"

    # Icons from release
    sudo cp "$EXTRACTED"/OtherMisc/icons/* "$REFIND_DIR/icons/" 2>/dev/null || true
    log_ok "RefindPlus icons installed"

    # Config (preserve existing; keep stock as the reference sample)
    if sudo test -f "$REFIND_DIR/refind.conf"; then
        sudo cp "$EXTRACTED/config.conf" "$REFIND_DIR/refind.conf.sample"
        log_ok "RefindPlus config exists; sample written"
    else
        sudo cp "$EXTRACTED/config.conf" "$REFIND_DIR/refind.conf"
        log_ok "RefindPlus config installed"
    fi

    # Repo overrides (optional)
    if [[ -d "$REPO_ROOT/system/refind/themes" ]]; then
        sudo mkdir -p "$REFIND_DIR/themes"
        sudo cp -r "$REPO_ROOT"/system/refind/themes/* "$REFIND_DIR/themes/" 2>/dev/null || true
    fi
    if [[ -d "$REPO_ROOT/system/refind/icons" ]]; then
        sudo cp "$REPO_ROOT"/system/refind/icons/* "$REFIND_DIR/icons/" 2>/dev/null || true
    fi

    echo "$VERSION" | sudo tee "$MARKER" >/dev/null
    log_ok "RefindPlus $VERSION installed"
fi

# --- Self-heal deployment: boot unit + apt hook ------------------------------
# This module's NVRAM maintenance only runs when install.py does, but the
# coups that demote RefindPlus happen at other times: the board firmware
# promotes the last-booted entry over it on every boot, and grub/shim
# package upgrades run grub-install in their postinst. Deploy a standalone
# assertion script plus two triggers for it (see script header for the
# residual one-boot gap on unhooked coup sources).

HEAL_SRC="$REPO_ROOT/system/systemd/system"

sudo install -m 755 "$HEAL_SRC/refindplus-assert-bootorder.sh" \
    /usr/local/sbin/refindplus-assert-bootorder
sudo install -m 644 "$HEAL_SRC/refindplus-bootorder.service" \
    /etc/systemd/system/refindplus-bootorder.service
if sudo install -m 644 "$REPO_ROOT/system/apt/apt.conf.d/99-refindplus-bootorder" \
    /etc/apt/apt.conf.d/99-refindplus-bootorder; then
    log_ok "BootOrder self-heal deployed (unit + apt hook)"
else
    log_warn "Failed to deploy apt hook — boot-time unit still active"
fi
sudo systemctl daemon-reload
if ! sudo systemctl enable refindplus-bootorder.service >/dev/null 2>&1; then
    log_warn "Could not enable refindplus-bootorder.service"
fi

# --- NVRAM maintenance ------------------------------------------------------

resolv_esp
if [[ -z "$DISK" || -z "$PART" ]]; then
    log_warn "Could not determine ESP disk/partition — skipping NVRAM maintenance"
elif [[ -z "$LIVE_GUID" ]]; then
    log_warn "Could not read live ESP PARTUUID — skipping NVRAM maintenance"
else
    refresh_efi_state
    if [[ -z "$EFI_STATE" ]]; then
        log_warn "efibootmgr returned no state — not a UEFI system? skipping NVRAM maintenance"
    else
        ACTION="validated"
        locate_entry
        if [[ -z "$ENTRY_NUM" ]]; then
            create_entry
            refresh_efi_state
            locate_entry
            ACTION="created"
            log_ok "RefindPlus NVRAM entry created (boot $ENTRY_NUM)"
        elif [[ -z "$ENTRY_GUID" || "${ENTRY_GUID,,}" != "${LIVE_GUID,,}" ]]; then
            sudo efibootmgr -b "$ENTRY_NUM" -B >/dev/null
            create_entry
            refresh_efi_state
            locate_entry
            ACTION="recreated"
            log_ok "RefindPlus NVRAM entry recreated — stale device path removed (boot $ENTRY_NUM)"
        fi
        if [[ -z "$ENTRY_NUM" ]]; then
            log_warn "RefindPlus NVRAM entry not resolvable after maintenance — check efibootmgr output"
        else
            order="$(sed -n 's/^BootOrder: //p' <<<"$EFI_STATE" | head -1 || true)"
            if [[ -z "$order" ]]; then
                log_warn "BootOrder not readable — cannot verify or re-assert order"
            else
                first="${order%%,*}"
                if [[ "${first,,}" == "${ENTRY_NUM,,}" ]]; then
                    log_ok "RefindPlus already first in BootOrder ($ACTION)"
                else
                    rest=""
                    IFS=',' read -ra items <<<"$order"
                    for item in "${items[@]}"; do
                        [[ "${item,,}" == "${ENTRY_NUM,,}" ]] && continue
                        rest+="${item},"
                    done
                    rest="${rest%,}"
                    sudo efibootmgr -o "${ENTRY_NUM},${rest}" >/dev/null
                    log_ok "BootOrder re-asserted: RefindPlus ($ENTRY_NUM) first"
                fi
            fi
        fi
    fi
fi

# Secure Boot warning
if cmd_exists mokutil; then
    if mokutil --sb-state 2>/dev/null | grep -qi "SecureBoot enabled"; then
        log_warn "Secure Boot appears enabled — RefindPlus binary may not be signed"
    fi
fi

exit 0
