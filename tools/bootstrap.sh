#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# tools/bootstrap.sh — stage-0 bootstrap (versioned copy; stick root holds
# the same content as `bootstrap.sh`).
#
# Invoke via `bash bootstrap.sh` (no exec bit assumed on exFAT; kernel
# readable since 5.4). Requires installer-time prerequisites: blank root
# password (sudo + sudo-group grant) and completed Reboot-1 manual WiFi fix.
# ---------------------------------------------------------------------------

print_wifi_help() {
    cat <<'EOF'
Manual WiFi fix (Reboot 1) — run once, then re-run bootstrap.sh.

Fast path (before pressing Reboot in the installer): Alt-F2,
  cp /etc/network/interfaces /target/etc/network/interfaces, Alt-F1, Reboot.

Otherwise, on first login:
  1. ip a — find iface (wlpXs0-style, never assume wlan0).
  2. Write /etc/network/interfaces (replace iface/SSID/pass):
       source /etc/network/interfaces.d/*
       auto lo
       iface lo inet loopback
       allow-hotplug <iface>
       iface <iface> inet dhcp
         wpa-ssid <SSID>
         wpa-psk <PASS>
     chmod 600 /etc/network/interfaces (hides PSK).
  3. sudo rfkill unblock wifi; sudo ifup <iface>
  4. Verify: ping -c3 9.9.9.9 and ping -c3 deb.debian.org must work.
     If not: ip a; iw dev; rfkill list; dmesg | grep -i firmware

Full commands: cat /mnt/ventoy/bootstrap-wifi.md
EOF
}

STICK_LABEL="Ventoy"
STICK_MOUNT="/mnt/ventoy"

# 1. Find stick by label; mount if needed (minimal install won't auto-mount).
if [[ ! -d "$STICK_MOUNT" ]]; then
    sudo mkdir -p "$STICK_MOUNT"
fi
if ! mountpoint -q "$STICK_MOUNT" 2>/dev/null; then
    stick_dev="$(blkid -L "$STICK_LABEL" 2>/dev/null || true)"
    if [[ -z "$stick_dev" ]]; then
        echo "ERROR: USB stick with label '$STICK_LABEL' not found." >&2
        echo "Confirm with: blkid -L $STICK_LABEL" >&2
        exit 1
    fi
    sudo mount "$stick_dev" "$STICK_MOUNT"
fi

# TEMPORARY evidence capture — delete with tools/capture-network-state.sh.
# Runs before stage-0 apt so it records the pristine post-installer state.
_cap="$STICK_MOUNT/capture-network-state.sh"
if [[ -r "$_cap" ]]; then
    bash "$_cap" || echo "WARN: network-state capture failed (continuing)" >&2
fi

# 2. Net preflight: this script cannot fix offline WiFi itself.
if ! timeout 5 getent hosts deb.debian.org &>/dev/null \
    || ! ping -c1 -W5 9.9.9.9 &>/dev/null; then
    print_wifi_help
    echo "--- diagnostics ---" >&2
    ip a >&2 || true
    iw dev >&2 || true
    rfkill list >&2 || true
    dmesg | grep -i firmware >&2 || true
    exit 1
fi

# 3. Assert sudo (cannot self-grant post-hoc — needs installer blank-root-pw).
if ! command -v sudo &>/dev/null; then
    echo "ERROR: sudo not installed. Reinstall with blank root password." >&2
    exit 1
fi
if ! sudo -v 2>/dev/null; then
    echo "ERROR: sudo not usable. Ensure user is in sudo group, then re-login:" >&2
    echo "  su -c 'usermod -aG sudo <user>' && logout" >&2
    exit 1
fi

# 4. Stage-0: minimal set BEFORE packages/ converge (single invocation —
# apt orders dependencies internally; verified no Pre-Depends chains).
sudo apt-get update && sudo apt-get install -y \
    sudo git make python3 curl ca-certificates \
    wpasupplicant wireless-tools iw rfkill xz-utils

# 5. Clone (idempotent), verify, and hand off only on consent.
REPO_URL="https://github.com/mk-sys-io/personal_machine_setup.git"
REPO_DIR="$HOME/linux_setup"   # canonical name; URL basename differs by design

if [[ -f "$REPO_DIR/install.py" ]]; then
    echo "Repo already present at $REPO_DIR — skipping clone."
elif [[ -e "$REPO_DIR" ]]; then
    echo "ERROR: $REPO_DIR exists but contains no install.py (empty or foreign dir)." >&2
    echo "Inspect, move or remove it, then re-run bootstrap.sh." >&2
    exit 1
else
    git clone "$REPO_URL" "$REPO_DIR"
    if [[ ! -f "$REPO_DIR/install.py" ]]; then
        echo "ERROR: install.py not found at $REPO_DIR/install.py — can't launch installer." >&2
        exit 1
    fi
fi

echo "Stage-0 complete: network OK, sudo OK, prerequisites installed, repo ready at $REPO_DIR."

if [[ -t 0 ]]; then
    _go=""
    read -r -p "Launch ./install.py now? (y/N): " _go || _go=""
    case "$_go" in
        y|Y)
            cd "$REPO_DIR" && exec ./install.py "$@"
            ;;
        *)
            echo "Skipped. When ready: cd ~/linux_setup && ./install.py"
            ;;
    esac
else
    echo "Non-interactive shell — not launching install.py."
    echo "When ready: cd ~/linux_setup && ./install.py"
fi
