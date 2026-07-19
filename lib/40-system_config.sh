#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 40-system_config.sh — System configuration
#
# DNS, podman DNS, dark mode, seal credential directories.
# set -euo pipefail handles hard failures (exit 1). Functions return 0 on
# skip (tool not available) which is not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# ---------------------------------------------------------------------------
# 1. DNS — NetworkManager dns=none + resolv.conf to local dnsmasq
# ---------------------------------------------------------------------------

setup_dns() {
    log_step "System DNS"

    # Tell NetworkManager not to manage DNS
    sudo mkdir -p /etc/NetworkManager/conf.d
    printf '[main]\ndns=none\n' | sudo tee /etc/NetworkManager/conf.d/90-dns-none.conf > /dev/null
    sudo chown root:root /etc/NetworkManager/conf.d/90-dns-none.conf
    sudo chmod 644 /etc/NetworkManager/conf.d/90-dns-none.conf
    log_ok "NetworkManager: dns=none"

    # Point resolv.conf to local dnsmasq
    sudo chattr -i /etc/resolv.conf 2>/dev/null || true
    echo 'nameserver 127.0.0.1' | sudo tee /etc/resolv.conf > /dev/null
    sudo chattr +i /etc/resolv.conf
    log_ok "resolv.conf: 127.0.0.1 (immutable)"
}

# ---------------------------------------------------------------------------
# 2. Podman DNS — containers use 1.1.1.1 directly
# ---------------------------------------------------------------------------

setup_podman_dns() {
    log_step "Podman DNS"

    sudo mkdir -p /etc/containers
    printf '[containers]\ndns_servers = ["%s"]\n' "$DNS_PRIMARY" | sudo tee /etc/containers/containers.conf > /dev/null
    sudo chown root:root /etc/containers/containers.conf
    sudo chmod 644 /etc/containers/containers.conf
    log_ok "Podman: container DNS set to $DNS_PRIMARY"
}

# ---------------------------------------------------------------------------
# 3. Dark mode — GTK color scheme preference
# ---------------------------------------------------------------------------

setup_dark_mode() {
    if ! cmd_exists gsettings; then
        log_warn "Dark mode: gsettings not found (libglib2.0-bin) — skipping"
        return 0
    fi

    log_step "Dark mode"
    gsettings set org.gnome.desktop.interface color-scheme prefer-dark 2>/dev/null || true
    log_ok "Dark mode: prefer-dark set"
}

# ---------------------------------------------------------------------------
# 4. Seal directories — credential files for seal/unseal system
# ---------------------------------------------------------------------------

setup_seal_dirs() {
    log_step "Seal directories"

    mkdir -p "$HOME/.config/seal"
    touch "$HOME/.config/seal/system.credentials" "$HOME/.config/seal/mobile.credentials"
    chmod 600 "$HOME/.config/seal/system.credentials" "$HOME/.config/seal/mobile.credentials"
    log_ok "Seal dirs: ~/.config/seal/ ready"
}

# ---------------------------------------------------------------------------
# 5. Udev rules — input device permissions (numlockwl)
# ---------------------------------------------------------------------------

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

    sudo udevadm control --reload-rules 2>/dev/null || true
    sudo udevadm trigger 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "System configuration"

setup_dns
setup_podman_dns
setup_dark_mode
setup_seal_dirs
setup_udev_rules

log_step "System config complete"
exit 0
