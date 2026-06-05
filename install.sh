#!/bin/bash

set -euo pipefail

echo "Installing dependencies..."

sudo apt install -y \
    sway \
    waybar \
    foot \
    foot-terminfo \
    wofi \
    network-manager \
    wl-clipboard \
    clipman \
    brightnessctl \
    wireplumber \
    fonts-jetbrains-mono

# Enable NetworkManager (required by waybar network module)
sudo systemctl enable --now NetworkManager 2>/dev/null || true

# Copy configs to their system locations
mkdir -p ~/.config/sway
mkdir -p ~/.config/waybar
mkdir -p ~/.config/foot
mkdir -p ~/.config/wofi
mkdir -p ~/.config/waybar/scripts

cp .config/sway_config ~/.config/sway/config
cp .config/waybar_config.json ~/.config/waybar/config.json
cp .config/style.css ~/.config/waybar/style.css
cp .config/waybar/mocha.css ~/.config/waybar/mocha.css
cp .config/foot.ini ~/.config/foot/foot.ini
cp .config/wofi-config ~/.config/wofi/config
cp .config/wofi-style.css ~/.config/wofi/style.css
cp .config/scripts/* ~/.config/waybar/scripts/
# Restore original Debian .bashrc from /etc/skel (overwrites any previous customization)
cp /etc/skel/.bashrc ~/.bashrc
echo "Restored original Debian .bashrc from /etc/skel/.bashrc"

# Append custom bashrc additions (idempotent — guarded by marker)
if ! grep -q "# linux_setup additions" ~/.bashrc 2>/dev/null; then
    cat .config/.bashrc >> ~/.bashrc
    echo "Appended custom bashrc additions"
else
    echo "Custom bashrc additions already present, skipping"
fi
