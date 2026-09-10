#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 40-system_config.sh — System configuration
#
# Dark mode, cask credential directories, sleep hook, udev rules.
# DNS setup moved to netmgr (lib/60-ark.sh deploy_system_dns).
# set -euo pipefail handles hard failures (exit 1). Functions return 0 on
# skip (tool not available) which is not a failure.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# ---------------------------------------------------------------------------
# 1. Dark mode — GTK color scheme preference
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
# 2. Cask directories — credential files for cask/uncask system
# ---------------------------------------------------------------------------

setup_cask_dirs() {
    log_step "Cask directories"

    # Migration: old ~/.local/share/seal → cask (S9). No-op on fresh installs.
    if [[ -d "$HOME/.local/share/seal" && ! -e "$HOME/.local/share/cask" ]]; then
        mv "$HOME/.local/share/seal" "$HOME/.local/share/cask"
        log "Migrated $HOME/.local/share/seal → $HOME/.local/share/cask"
    fi

    sudo mkdir -p "$ARK_DATA_PATH/cask"
    sudo chown root:root "$ARK_DATA_PATH/cask"
    sudo chmod 750 "$ARK_DATA_PATH/cask"
    mkdir -p "$HOME/.local/share/cask"
    touch "$HOME/.local/share/cask/system.credentials" "$HOME/.local/share/cask/mobile.credentials"
    chmod 600 "$HOME/.local/share/cask/system.credentials" "$HOME/.local/share/cask/mobile.credentials"
    sudo mkdir -p "$ARK_DATA_PATH/logs"
    sudo chown root:root "$ARK_DATA_PATH/logs"
    sudo chmod 750 "$ARK_DATA_PATH/logs"
    log_ok "Cask dirs: $ARK_DATA_PATH/cask/ (casked) + ~/.local/share/cask/ (working)"
}

# ---------------------------------------------------------------------------
# 3. Systemd sleep hook — re-evaluate wlsunset after suspend/resume
# ---------------------------------------------------------------------------

setup_sleep_hook() {
    log_step "Systemd sleep hook"

    local src="$REPO_ROOT/system/systemd/system-sleep/wlsunset-resume.sh"
    local dst="/usr/lib/systemd/system-sleep/wlsunset-resume.sh"
    if [[ ! -f "$src" ]]; then
        log_warn "Sleep hook: source not found — skipping"
        return 0
    fi
    sudo mkdir -p /usr/lib/systemd/system-sleep
    if ! sudo cmp -s "$src" "$dst" 2>/dev/null; then
        sudo cp "$src" "$dst"
        sudo chmod 755 "$dst"
        log_ok "Sleep hook: wlsunset-resume.sh deployed"
    else
        log_ok "Sleep hook: wlsunset-resume.sh already up to date"
    fi
}

# ---------------------------------------------------------------------------
# 4. Udev rules — input device permissions (numlockwl)
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
# 5. Polkit rules — libvirt group read-only access (vm view)
# ---------------------------------------------------------------------------

setup_polkit_rules() {
    log_step "Polkit rules"

    local rules_dir="$REPO_ROOT/system/polkit-1/rules.d"
    if [[ ! -d "$rules_dir" ]]; then
        log_warn "Polkit rules: source dir not found — skipping"
        return 0
    fi

    sudo mkdir -p /etc/polkit-1/rules.d
    for rule in "$rules_dir"/*.rules; do
        [[ -f "$rule" ]] || continue
        local name
        name=$(basename "$rule")
        if ! sudo cmp -s "$rule" "/etc/polkit-1/rules.d/$name" 2>/dev/null; then
            sudo cp "$rule" "/etc/polkit-1/rules.d/$name"
            sudo chmod 644 "/etc/polkit-1/rules.d/$name"
            log_ok "Polkit rule: $name deployed"
        else
            log_ok "Polkit rule: $name already up to date"
        fi
    done

    sudo systemctl reload polkit 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log_step "System configuration"

setup_dark_mode
setup_cask_dirs
setup_sleep_hook
setup_udev_rules
setup_polkit_rules

log_step "System config complete"
exit 0
