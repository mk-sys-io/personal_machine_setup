#!/bin/bash

set -euo pipefail

echo "Installing dependencies..."

# Install gnupg early (needed for Brave repo key import)
sudo apt install -y gnupg curl

# Brave Browser apt repo
curl -fsSL https://brave-browser-apt-release.s3.brave.com/brave-core.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/brave-browser-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/brave-browser-archive-keyring.gpg] \
  https://brave-browser-apt-release.s3.brave.com/ stable main" \
  | sudo tee /etc/apt/sources.list.d/brave-browser-release.list
sudo apt update

sudo apt install -y \
    sway \
    waybar \
    foot \
    foot-terminfo \
    fuzzel \
    network-manager \
    wl-clipboard \
    copyq \
    copyq-plugins \
    brightnessctl \
    wireplumber \
    fonts-jetbrains-mono \
    git \
    python3 \
    timeshift \
    brave-browser \
    curl \
    libglib2.0-bin

# Enable NetworkManager (required by waybar network module)
sudo systemctl enable --now NetworkManager 2>/dev/null || true

# =========================================================================
# YDOTOOL (Wayland keystroke injection for CopyQ auto-paste)
# =========================================================================

echo "Installing ydotool..."

YDOTOOL_VER=v1.0.4
YDOTOOL_BASE=https://github.com/ReimuNotMoe/ydotool/releases/download/$YDOTOOL_VER

sudo curl -fsSL -o /usr/local/bin/ydotool \
  "$YDOTOOL_BASE/ydotool-release-ubuntu-latest"
sudo chmod +x /usr/local/bin/ydotool

sudo curl -fsSL -o /usr/local/sbin/ydotoold \
  "$YDOTOOL_BASE/ydotoold-release-ubuntu-latest"
sudo chmod +x /usr/local/sbin/ydotoold

# =========================================================================
# WORK TOOLS
# =========================================================================

echo "Installing work tools..."

# Install Node.js (general utility)
if ! command -v node &>/dev/null; then
    sudo apt install -y nodejs npm || true
fi

# Install Zed IDE
if ! command -v zed &>/dev/null; then
    curl https://zed.dev/install.sh 2>/dev/null | sh || true
fi

# Install OpenCode AI agent
if ! command -v opencode &>/dev/null; then
    curl -fsSL https://opencode.ai/install | bash || true
fi

# =========================================================================

# Copy configs to their system locations
mkdir -p ~/.config/sway
mkdir -p ~/.config/waybar
mkdir -p ~/.config/foot
mkdir -p ~/.config/fuzzel
mkdir -p ~/.config/copyq/themes
mkdir -p ~/.config/waybar/scripts

cp .config/sway/sway_config ~/.config/sway/config
cp .config/waybar/waybar_config.json ~/.config/waybar/config.json
cp .config/waybar/style.css ~/.config/waybar/style.css
cp .config/waybar/mocha.css ~/.config/waybar/mocha.css
cp .config/foot/foot.ini ~/.config/foot/foot.ini
cp .config/fuzzel/fuzzel.ini ~/.config/fuzzel/fuzzel.ini
cp .config/copyq/copyq.conf ~/.config/copyq/copyq.conf
cp .config/copyq/themes/* ~/.config/copyq/themes/
cp .config/scripts/* ~/.config/waybar/scripts/

# =========================================================================
# BRAVE ENTERPRISE POLICY (Debloated browser configuration)
# =========================================================================

sudo mkdir -p /etc/brave/policies/managed
sudo cp .config/brave/kiosk_policy.json /etc/brave/policies/managed/kiosk_policy.json
sudo chmod 644 /etc/brave/policies/managed/kiosk_policy.json

# =========================================================================

# Append custom bashrc additions (idempotent — guarded by marker)
if ! grep -q "# --- linux_setup additions ---" ~/.bashrc 2>/dev/null; then
    cat .config/bashrc >> ~/.bashrc
    echo "Appended custom bashrc additions"
else
    echo "Custom bashrc additions already present, skipping"
fi

# =========================================================================
# DARK MODE (System-wide color scheme preference)
# =========================================================================

# Set GTK/Brave color scheme to dark (requires dconf, which is auto-started by Sway)
gsettings set org.gnome.desktop.interface color-scheme prefer-dark 2>/dev/null || true
echo "System dark mode preference set"
