#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 30-hardware.sh — Hardware configuration
#
# Backlight, WiFi power management, nouveau GSP firmware.
# Each function checks for hardware presence before acting.
# set -euo pipefail handles hard failures (exit 1). Functions return 0 on
# skip (no hardware / already configured) which is not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

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
}

# ---------------------------------------------------------------------------
# 2. WiFi power — Intel AX201 stability
# ---------------------------------------------------------------------------

setup_wifi_power() {
    log "WiFi: disabling power management..."

    printf 'options iwlwifi power_save=0 uapsd_disable=1\noptions iwlmvm power_scheme=1\n' \
        | sudo tee /etc/modprobe.d/iwlwifi-opt.conf > /dev/null
    sudo chmod 644 /etc/modprobe.d/iwlwifi-opt.conf

    printf '[connection]\nwifi.powersave=2\n' \
        | sudo tee /etc/NetworkManager/conf.d/90-wifi-power-save.conf > /dev/null
    sudo chmod 644 /etc/NetworkManager/conf.d/90-wifi-power-save.conf

    log_ok "WiFi: AX201 power management disabled (modprobe + NM)"
}

# ---------------------------------------------------------------------------
# 3. Nouveau GSP — GPU power management
# ---------------------------------------------------------------------------

setup_nouveau_gsp() {
    local grub_cfg="/etc/default/grub.d/99-nouveau-gsp.cfg"
    local src_cfg="$REPO_ROOT/lockdown/grub.d/99-nouveau-gsp.cfg"

    if [[ -f "$grub_cfg" ]] && grep -q 'nouveau.config=NvGspRm=1' "$grub_cfg" 2>/dev/null; then
        log_ok "Nouveau GSP: already configured"
        return 0
    fi

    if [[ ! -f "$src_cfg" ]]; then
        log_warn "Nouveau GSP: source $src_cfg not found — skipping"
        return 0
    fi

    log "Nouveau GSP: installing kernel param..."
    sudo mkdir -p /etc/default/grub.d
    sudo cp "$src_cfg" "$grub_cfg"
    sudo chown root:root "$grub_cfg"
    sudo chmod 644 "$grub_cfg"
    sudo update-grub
    needs_reboot
    log_ok "Nouveau GSP: kernel param added (reboot required)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log "=== Hardware configuration ==="

setup_backlight
setup_wifi_power
setup_nouveau_gsp

log "=== Hardware: done ==="
exit 0
