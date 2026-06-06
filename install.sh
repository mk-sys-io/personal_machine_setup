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
    fonts-jetbrains-mono \
    git \
    python3 \
    timeshift \
    chromium \
    curl

# Enable NetworkManager (required by waybar network module)
sudo systemctl enable --now NetworkManager 2>/dev/null || true

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
mkdir -p ~/.config/wofi
mkdir -p ~/.config/waybar/scripts

cp .config/sway/sway_config ~/.config/sway/config
cp .config/waybar/waybar_config.json ~/.config/waybar/config.json
cp .config/waybar/style.css ~/.config/waybar/style.css
cp .config/waybar/mocha.css ~/.config/waybar/mocha.css
cp .config/foot/foot.ini ~/.config/foot/foot.ini
cp .config/wofi/wofi-config ~/.config/wofi/config
cp .config/wofi/wofi-style.css ~/.config/wofi/style.css
cp .config/scripts/* ~/.config/waybar/scripts/

# =========================================================================
# CHROMIUM ENTERPRISE POLICY (Hardened browser configuration)
# =========================================================================

sudo mkdir -p /etc/chromium/policies/managed
cat > /tmp/kiosk_policy.json << 'EOF'
{
  "IncognitoModeAvailability": 1,
  "BrowserGuestModeEnabled": false,
  "ExtensionSettings": {
    "*": {
      "installation_mode": "blocked",
      "blocked_install_message": "Only administrator-approved extensions are permitted."
    },
    "cjpalhdlnbpafiamejdnhcphjbkeiagm": {
      "installation_mode": "force_installed",
      "update_url": "https://clients2.google.com/service/update2/crx"
    },
    "nngcegbndaddmdaobaadofmlidjmjhna": {
      "installation_mode": "force_installed",
      "update_url": "https://clients2.google.com/service/update2/crx"
    },
    "pkehgijbbdfpndfillndmdaidbpeboom": {
      "installation_mode": "force_installed",
      "update_url": "https://clients2.google.com/service/update2/crx"
    }
  }
}
EOF
sudo cp /tmp/kiosk_policy.json /etc/chromium/policies/managed/kiosk_policy.json
sudo chmod 644 /etc/chromium/policies/managed/kiosk_policy.json

# =========================================================================

# Append custom bashrc additions (idempotent — guarded by marker)
if ! grep -q "# linux_setup additions" ~/.bashrc 2>/dev/null; then
    cat .config/bashrc >> ~/.bashrc
    echo "Appended custom bashrc additions"
else
    echo "Custom bashrc additions already present, skipping"
fi
