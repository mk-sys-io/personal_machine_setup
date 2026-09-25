#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 30-hardware.sh — Hardware configuration
#
# Backlight, WiFi power management, Bluetooth, and input udev rules.
# The udev section runs everywhere (including VMs): numlockwl enforces
# guest numlock via a virtual keyboard (uinput), and SPICE LED sync covers
# runtime toggles only — the guest boots with numlock off.
# The host-hardware sections below skip inside VMs.
# Each function checks for hardware presence before acting.
# set -euo pipefail handles hard failures (exit 1). Functions return 0 on
# skip (no hardware / already configured) which is not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers/common.sh"

# ---------------------------------------------------------------------------
# 0. Udev rules — uinput access for numlockwl's virtual keyboard
# ---------------------------------------------------------------------------
# Deliberately above the is_vm gate: sway runs in the staging VM too
# (dotfiles/sway/config exec_always numlockwl --on), and whether SPICE LED
# sync covers a given session is undocumented — the rule is harmless if
# redundant, and its absence fails silently (wrong LED, no error).
# Moved from 40-system_config.sh (dissolve: single /etc/udev/rules.d owner).

setup_udev_rules() {
    log_step "Udev rules"

    local rules_dir="$REPO_ROOT/system/udev/rules.d"
    if [[ ! -d "$rules_dir" ]]; then
        log_warn "Udev rules: source dir not found — skipping"
        return 0
    fi

    sudo mkdir -p /etc/udev/rules.d
    for rule in "$rules_dir"/*.rules; do
        [[ -f "$rule" ]] || continue
        local name
        name=$(basename "$rule")
        if ! sudo cmp -s "$rule" "/etc/udev/rules.d/$name" 2>/dev/null; then
            sudo cp "$rule" "/etc/udev/rules.d/$name"
            sudo chmod 644 "/etc/udev/rules.d/$name"
            log_ok "Udev rule: $name deployed"
        else
            log_ok "Udev rule: $name already up to date"
        fi
    done

    sudo udevadm control --reload-rules 2>/dev/null || log_warn "udev: control --reload-rules failed"
    sudo udevadm trigger 2>/dev/null || log_warn "udev: trigger failed"
}

setup_udev_rules

# Never run host-hardware config inside a VM: backlight/wifi/btusb targets
# don't exist on virtio, and setup_btusb_nosleep would set the reboot marker
# (needs_reboot) and trigger install.py's reboot prompt in the VM.
if is_vm; then
    log "SKIP: running inside a VM — host-hardware config not applicable"
    exit 2
fi

# ---------------------------------------------------------------------------
# 1. Backlight — systemd-backlight auto-detection
# ---------------------------------------------------------------------------

setup_backlight() {
    local unit_path=""

    if [[ -f /usr/lib/systemd/system/systemd-backlight@.service ]]; then
        unit_path=/usr/lib/systemd/system/systemd-backlight@.service
    elif [[ -f /lib/systemd/system/systemd-backlight@.service ]]; then
        unit_path=/lib/systemd/system/systemd-backlight@.service
    fi

    if [[ -z "$unit_path" ]]; then
        log_warn "Backlight: systemd-backlight@.service not found — skipping"
        return 0
    fi

    log_ok "Backlight: systemd-backlight@.service found"

    local found=false
    for dev in /sys/class/backlight/*; do
        [[ -e "$dev" ]] || continue
        found=true
        local dev_name
        dev_name=$(basename "$dev")
        local service="systemd-backlight@backlight:${dev_name}.service"

        if systemctl is-enabled "$service" &>/dev/null; then
            log_ok "Backlight: $dev_name already enabled"
        else
            sudo systemctl enable "$service"
            log_ok "Backlight: $dev_name enabled"
        fi
    done

    if [[ "$found" == false ]]; then
        log_warn "Backlight: no backlight devices found — skipping"
        return 0
    fi

    log_ok "Backlight: setup complete"
}

# ---------------------------------------------------------------------------
# 2. Backlight — disable 5% minimum clamp (systemd 257)
# ---------------------------------------------------------------------------

setup_backlight_noclamp() {
    log_step "Backlight: disable brightness clamp"

    local rule_path="/etc/udev/rules.d/91-backlight-noclamp.rules"
    local rule='ACTION=="add", SUBSYSTEM=="backlight", ENV{ID_BACKLIGHT_CLAMP}="false"'

    if [[ -f "$rule_path" ]] && grep -qF 'ID_BACKLIGHT_CLAMP.*false' "$rule_path"; then
        log_ok "Backlight: noclamp rule already present"
        return 0
    fi

    echo "$rule" | sudo tee "$rule_path" > /dev/null
    sudo chmod 644 "$rule_path"
    sudo udevadm control --reload-rules && sudo udevadm trigger

    log_ok "Backlight: clamp disabled (udev rule applied)"
}

# ---------------------------------------------------------------------------
# 3. WiFi power — Intel AX201 stability
# ---------------------------------------------------------------------------

setup_wifi_power() {
    log_step "WiFi power management"

    printf 'options iwlwifi power_save=0 uapsd_disable=1\noptions iwlmvm power_scheme=1\n' \
        | sudo tee /etc/modprobe.d/iwlwifi-opt.conf > /dev/null
    sudo chmod 644 /etc/modprobe.d/iwlwifi-opt.conf

    printf '[connection]\nwifi.powersave=2\n' \
        | sudo tee /etc/NetworkManager/conf.d/90-wifi-power-save.conf > /dev/null
    sudo chmod 644 /etc/NetworkManager/conf.d/90-wifi-power-save.conf

    log_ok "WiFi: AX201 power management disabled (modprobe + NM)"
}

# ---------------------------------------------------------------------------
# 4. Bluetooth — disable USB autosuspend for Intel AX201 / btusb
# ---------------------------------------------------------------------------

setup_btusb_nosleep() {
    log_step "Bluetooth: disable USB autosuspend"
    sudo mkdir -p /etc/modprobe.d
    if ! sudo cmp -s "$REPO_ROOT/system/modprobe.d/btusb-disable-autosuspend.conf" /etc/modprobe.d/btusb-disable-autosuspend.conf 2>/dev/null; then
        sudo cp "$REPO_ROOT/system/modprobe.d/btusb-disable-autosuspend.conf" /etc/modprobe.d/
        log_ok "Bluetooth: btusb autosuspend disabled (modprobe)"
        needs_reboot
    else
        log_ok "Bluetooth: btusb autosuspend already disabled"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "Hardware configuration"

setup_backlight
setup_backlight_noclamp
setup_wifi_power
setup_btusb_nosleep

log_step "Hardware complete"
exit 0
