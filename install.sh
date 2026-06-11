#!/bin/bash

set -euo pipefail

echo "Installing dependencies..."

# Install gnupg early (needed for Brave repo key import)
sudo apt install -y gnupg curl

# Brave Browser apt repo
curl -fsSL https://brave-browser-apt-release.s3.brave.com/brave-core.asc \
  | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/brave-browser-archive-keyring.gpg
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
    glow \
    brightnessctl \
    wireplumber \
    fonts-jetbrains-mono \
    git \
    python3 \
    timeshift \
    brave-browser \
    curl \
    libglib2.0-bin \
    nftables \
    golang-go

# Enable NetworkManager (required by waybar network module)
sudo systemctl enable --now NetworkManager 2>/dev/null || true

# Enable nftables (kernel firewall — base skeleton with no restrictions)
sudo systemctl enable --now nftables 2>/dev/null || true

# Install podman (container runtime — no polkit dependency)
if command -v podman &>/dev/null; then
    echo "podman already installed, skipping"
else
    echo "Installing podman..."
    sudo apt install -y podman
fi

# =========================================================================
# YDOTOOL (Wayland keystroke injection for CopyQ auto-paste)
# =========================================================================

if command -v ydotool &>/dev/null; then
    echo "ydotool already installed, skipping"
else
    echo "Installing ydotool..."

    YDOTOOL_VER=v1.0.4
    YDOTOOL_BASE=https://github.com/ReimuNotMoe/ydotool/releases/download/$YDOTOOL_VER

    sudo curl -fsSL -o /usr/local/bin/ydotool \
      "$YDOTOOL_BASE/ydotool-release-ubuntu-latest"
    sudo chmod +x /usr/local/bin/ydotool

    sudo curl -fsSL -o /usr/local/sbin/ydotoold \
      "$YDOTOOL_BASE/ydotoold-release-ubuntu-latest"
    sudo chmod +x /usr/local/sbin/ydotoold
fi

# =========================================================================
# NEXTDNS CLI (DNS-level filtering — non-interactive install)
# =========================================================================

if command -v nextdns &>/dev/null; then
    echo "NextDNS CLI already installed, skipping"
else
    echo "Installing NextDNS CLI..."
    NEXTDNS_DEB_URL=$(curl -sL "https://api.github.com/repos/nextdns/nextdns/releases/latest" \
      | grep -oP '"browser_download_url":\s*"\K[^"]*linux_amd64\.deb[^"]*')
    curl -fsSL -o /tmp/nextdns_linux_amd64.deb "$NEXTDNS_DEB_URL"
    sudo dpkg -i /tmp/nextdns_linux_amd64.deb
    rm -f /tmp/nextdns_linux_amd64.deb
fi

# =========================================================================

# Append custom bashrc additions (idempotent — guarded by marker)
if ! grep -q "# --- linux_setup additions ---" ~/.bashrc 2>/dev/null; then
    cat .config/bashrc >> ~/.bashrc
    source ~/.bashrc
    echo "Appended custom bashrc additions"
else
    echo "Custom bashrc additions already present, skipping"
fi

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

# Install tle (time-locked encryption for Phase 4)
if [ ! -x ~/go/bin/tle ]; then
    go install github.com/drand/tlock/cmd/tle@latest || true
fi
# Make tle available at a stable system path for root usage
if [ -x ~/go/bin/tle ] && [ ! -f /usr/local/bin/tle ]; then
    sudo cp ~/go/bin/tle /usr/local/bin/tle
    echo "tle copied to /usr/local/bin/tle"
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
# POLICY KIT LOCKDOWN (Block pkexec for user mike — permanent)
# =========================================================================

sudo mkdir -p /etc/polkit-1/rules.d
sudo cp .config/polkit/99-internet-lockdown.rules /etc/polkit-1/rules.d/99-internet-lockdown.rules
sudo chown root:root /etc/polkit-1/rules.d/99-internet-lockdown.rules
sudo chmod 644 /etc/polkit-1/rules.d/99-internet-lockdown.rules
echo "PolicyKit: pkexec blocked for user mike"

# =========================================================================
# DARK MODE (System-wide color scheme preference)
# =========================================================================

# Set GTK/Brave color scheme to dark (requires dconf, which is auto-started by Sway)
gsettings set org.gnome.desktop.interface color-scheme prefer-dark 2>/dev/null || true
echo "System dark mode preference set"

# =========================================================================
# ROOT-OWNED ALLOWLIST UTILITY
# =========================================================================

sudo mkdir -p /opt/allowlist
sudo cp .config/scripts/allowlist.sh /opt/allowlist/
sudo cp .config/scripts/generate-policies.sh /opt/allowlist/
sudo cp .config/scripts/generate-nftables.sh /opt/allowlist/
sudo cp .config/scripts/verify.sh /opt/allowlist/
sudo cp .config/allowlist.txt /opt/allowlist/
sudo cp .config/nftables/nftables.conf.base /opt/allowlist/
sudo cp .config/nftables/nftables.conf.locked /opt/allowlist/
sudo cp .config/brave/policy.json.template /opt/allowlist/brave-policy.json.template
sudo cp .config/firefox/policies.json.template /opt/allowlist/firefox-policies.json.template
sudo chown -R root:root /opt/allowlist
sudo chmod 755 /opt/allowlist/*.sh
sudo chmod 644 /opt/allowlist/allowlist.txt
sudo chmod 644 /opt/allowlist/brave-policy.json.template /opt/allowlist/firefox-policies.json.template
sudo chmod 644 /opt/allowlist/nftables.conf.base /opt/allowlist/nftables.conf.locked

# Remove stale copies from old home-dir layout
rm -f ~/.config/waybar/scripts/allowlist.sh
rm -f ~/.config/waybar/scripts/generate-policies.sh
rm -f ~/.config/waybar/scripts/generate-nftables.sh
rm -f ~/.config/waybar/scripts/verify.sh
rm -f ~/.config/waybar/scripts/nftables.conf.*
rm -f ~/.config/allowlist-mode

echo "Allowlist utility deployed to /opt/allowlist/"

# =========================================================================
# NFTABLES BASE (Starting state — DNS working, no restrictions)
# =========================================================================

sudo cp .config/nftables/nftables.conf.base /etc/nftables.conf
sudo chown root:root /etc/nftables.conf
sudo chmod 644 /etc/nftables.conf
sudo systemctl restart nftables

# =========================================================================
# POST-INSTALL MANUAL STEPS SIGNAL
# =========================================================================

echo ""
tput bold 2>/dev/null && tput setaf 3 2>/dev/null
echo "================================================"
echo "  INSTALL COMPLETE — Manual setup required"
echo "================================================"
tput sgr0 2>/dev/null
echo ""
echo "  The system is installed but not yet locked."
echo "  See manual_work.md for the post-install steps:"
echo "    glow manual_work.md"
echo ""
