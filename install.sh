#!/bin/bash

set -euo pipefail

echo "Installing dependencies..."

# Install gnupg and curl early (needed for Brave repo key import)
sudo apt install -y gnupg curl

APT_PACKAGES=(
    sway waybar foot foot-terminfo fuzzel
    network-manager wl-clipboard copyq copyq-plugins
    glow brightnessctl wireplumber fonts-jetbrains-mono
    git python3 timeshift libglib2.0-bin
    nftables dnsmasq golang-go podman vlc
    nodejs npm
)

MISSING=()
for pkg in "${APT_PACKAGES[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        echo "  $pkg already installed"
    else
        MISSING+=("$pkg")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  Installing: ${MISSING[*]}"
    sudo apt install -y "${MISSING[@]}"
else
    echo "  All packages already installed"
fi

# Enable NetworkManager (required by waybar network module)
sudo systemctl enable --now NetworkManager 2>/dev/null || true

# Enable nftables (kernel firewall — base skeleton with no restrictions)
sudo systemctl enable --now nftables 2>/dev/null || true

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
# BRAVE BROWSER (skip if already installed — separate from main apt batch)
# =========================================================================

if command -v brave-browser &>/dev/null; then
    echo "Brave already installed, skipping"
else
    echo "Installing Brave Browser..."
    curl -fsSL https://brave-browser-apt-release.s3.brave.com/brave-core.asc \
      | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/brave-browser-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/brave-browser-archive-keyring.gpg] \
      https://brave-browser-apt-release.s3.brave.com/ stable main" \
      | sudo tee /etc/apt/sources.list.d/brave-browser-release.list
    sudo apt update
    sudo apt install -y brave-browser
fi

# =========================================================================
# GOOGLE CHROME (needed for sites where Brave layout breaks — e.g. UoPeople)
# =========================================================================

if command -v google-chrome-stable &>/dev/null; then
    echo "Chrome already installed, skipping"
else
    echo "Installing Google Chrome..."
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
      | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] \
      http://dl.google.com/linux/chrome/deb/ stable main" \
      | sudo tee /etc/apt/sources.list.d/google-chrome.list
    sudo apt update
    sudo apt install -y google-chrome-stable
fi

# =========================================================================
# SYSTEM DNS CONFIGURATION
# =========================================================================

# Configure system DNS to use local dnsmasq
sudo mkdir -p /etc/NetworkManager/conf.d
printf '[main]\ndns=none\n' | sudo tee /etc/NetworkManager/conf.d/90-dns-none.conf > /dev/null
sudo chown root:root /etc/NetworkManager/conf.d/90-dns-none.conf
sudo chmod 644 /etc/NetworkManager/conf.d/90-dns-none.conf
echo "NetworkManager: dns=none (will not overwrite resolv.conf)"

sudo chattr -i /etc/resolv.conf 2>/dev/null || true
echo 'nameserver 127.0.0.1' | sudo tee /etc/resolv.conf > /dev/null
sudo chattr +i /etc/resolv.conf
echo "resolv.conf: set to 127.0.0.1 and made immutable"

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

# Install localsend (not in Debian repos — download .deb from GitHub)
if ! dpkg -s localsend &>/dev/null 2>&1; then
    echo "Installing LocalSend..."
    LS_DEB=$(mktemp)
    LS_URL=$(curl -s https://api.github.com/repos/localsend/localsend/releases/latest \
        | grep "browser_download_url.*linux-x86-64\.deb" \
        | cut -d '"' -f 4)
    if [ -n "$LS_URL" ]; then
        curl -fsSL -o "$LS_DEB" "$LS_URL"
        sudo apt install -y libayatana-appindicator3-1 gir1.2-ayatanaappindicator3-0.1 libayatana-ido3-0.4-0 xdg-user-dirs
        sudo dpkg -i "$LS_DEB"
        rm -f "$LS_DEB"
    else
        echo "  WARNING: Could not determine latest LocalSend URL, skipping"
    fi
fi

# Install Obsidian (not in Debian repos — download .deb from GitHub)
if ! dpkg -s obsidian &>/dev/null 2>&1; then
    echo "Installing Obsidian..."
    OBS_DEB=$(mktemp)
    OBS_URL=$(curl -s https://api.github.com/repos/obsidianmd/obsidian-releases/releases/latest \
        | grep "browser_download_url.*amd64\.deb" \
        | cut -d '"' -f 4)
    if [ -n "$OBS_URL" ]; then
        curl -fsSL -o "$OBS_DEB" "$OBS_URL"
        sudo apt install -y libxss1
        sudo dpkg -i "$OBS_DEB"
        rm -f "$OBS_DEB"
    else
        echo "  WARNING: Could not determine latest Obsidian URL, skipping"
    fi
fi

# Copy Obsidian vault dark mode config
cp .config/obsidian/appearance.json "/home/mike/Obsidian Vault/.obsidian/appearance.json"
echo "Obsidian vault: dark theme applied"

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
sudo cp .config/scripts/generate-dnsmasq.sh /opt/allowlist/
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

echo "Allowlist utility deployed to /opt/allowlist/"

# =========================================================================
# DNSMASQ (Local DNS proxy — generate initial config and enable)
# =========================================================================

sudo mkdir -p /etc/dnsmasq.d
sudo /opt/allowlist/generate-dnsmasq.sh unrestricted
sudo systemctl enable --now dnsmasq
echo "dnsmasq: enabled and started (unrestricted mode)"

# =========================================================================
# NFTABLES BASE (Starting state — DNS working, no restrictions)
# =========================================================================

sudo cp .config/nftables/nftables.conf.base /etc/nftables.conf
sudo chown root:root /etc/nftables.conf
sudo chmod 644 /etc/nftables.conf
sudo systemctl restart nftables

# =========================================================================
# PODMAN DEFAULT DNS (containers use 1.1.1.1 directly, not host dnsmasq)
# =========================================================================

sudo mkdir -p /etc/containers
printf '[containers]\ndns_servers = ["1.1.1.1"]\n' | sudo tee /etc/containers/containers.conf > /dev/null
sudo chown root:root /etc/containers/containers.conf
sudo chmod 644 /etc/containers/containers.conf
echo "podman: default container DNS set to 1.1.1.1"

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
