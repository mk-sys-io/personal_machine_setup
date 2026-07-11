#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 40-system_config.sh — System configuration
#
# DNS, podman DNS, dark mode, seal credential directories.
# Exit 0 = pass, exit 1 = failure
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RC=0

# ---------------------------------------------------------------------------
# 1. DNS — NetworkManager dns=none + resolv.conf to local dnsmasq
# ---------------------------------------------------------------------------

setup_dns() {
    log "Configuring system DNS..."

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
    log "Configuring podman DNS..."

    sudo mkdir -p /etc/containers
    printf '[containers]\ndns_servers = ["1.1.1.1"]\n' | sudo tee /etc/containers/containers.conf > /dev/null
    sudo chown root:root /etc/containers/containers.conf
    sudo chmod 644 /etc/containers/containers.conf
    log_ok "Podman: container DNS set to 1.1.1.1"
}

# ---------------------------------------------------------------------------
# 3. Dark mode — GTK color scheme preference
# ---------------------------------------------------------------------------

setup_dark_mode() {
    if ! cmd_exists gsettings; then
        log_warn "Dark mode: gsettings not found (libglib2.0-bin) — skipping"
        return 0
    fi

    log "Setting dark mode preference..."
    gsettings set org.gnome.desktop.interface color-scheme prefer-dark 2>/dev/null || true
    log_ok "Dark mode: prefer-dark set"
}

# ---------------------------------------------------------------------------
# 4. Seal directories — credential files for seal/unseal system
# ---------------------------------------------------------------------------

setup_seal_dirs() {
    log "Creating seal credential directories..."

    mkdir -p "$HOME/.config/seal"
    touch "$HOME/.config/seal/system.credentials" "$HOME/.config/seal/mobile.credentials"
    chmod 600 "$HOME/.config/seal/system.credentials" "$HOME/.config/seal/mobile.credentials"
    log_ok "Seal dirs: ~/.config/seal/ ready"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log "=== System configuration ==="

setup_dns
setup_podman_dns
setup_dark_mode
setup_seal_dirs

log "=== System config: done ==="
exit $RC
