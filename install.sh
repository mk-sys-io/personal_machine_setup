#!/bin/bash

set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "ERROR: This script must NOT be run as root."
    echo "  Run it as a regular user with sudo privileges:"
    echo "  ./install.sh"
    exit 1
fi

if ! groups | grep -q '\bsudo\b'; then
    echo "ERROR: User '$USER' is not in the sudo group."
    echo "  Run: sudo usermod -aG sudo $USER"
    echo "  Then log out and back in."
    exit 1
fi

echo "Checking network prerequisites..."

if ! timeout 5 getent hosts raw.githubusercontent.com &>/dev/null; then
    echo "ERROR: DNS resolution failed (cannot resolve raw.githubusercontent.com)."
    echo "  Check /etc/resolv.conf and network connectivity."
    exit 1
fi

if ! timeout 5 bash -c 'echo > /dev/tcp/raw.githubusercontent.com/443' 2>/dev/null; then
    echo "ERROR: No internet connectivity (cannot reach raw.githubusercontent.com:443)."
    echo "  Check your network connection."
    exit 1
fi

echo "Network prerequisites satisfied."

# =========================================================================
# SUDO — acquire credentials now and keep alive for entire script
# =========================================================================

sudo -v
KEEPALIVE_PID=""
trap "kill \$KEEPALIVE_PID 2>/dev/null; sudo -k" EXIT INT TERM
while true; do sudo -nv || true; sleep 60; done &
KEEPALIVE_PID=$!

NEEDS_REBOOT=false

# =========================================================================
# GITHUB CREDENTIALS — load env file or prompt for missing values
# =========================================================================

ENV_FILE=".config/github.env"

if [ ! -f "$ENV_FILE" ]; then
    cp .config/github.env.template "$ENV_FILE"
    echo "Created $ENV_FILE from template"
fi

# shellcheck source=.config/github.env
source "$ENV_FILE"

MODIFIED=false

if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "GitHub credentials needed for:"
    echo "  - gh CLI authentication (stores token for HTTPS Git operations)"
    echo "  - Git user identity (name + email)"
    echo "  - Authenticated API calls (bypasses 60 req/h rate limit)"
    while [ -z "$GITHUB_TOKEN" ]; do
        read -rsp "  GITHUB_TOKEN: " GITHUB_TOKEN
        echo
        [ -z "$GITHUB_TOKEN" ] && echo "  Token cannot be empty, try again."
    done
    MODIFIED=true
fi

if [ -z "${GIT_USER_NAME:-}" ]; then
    while [ -z "$GIT_USER_NAME" ]; do
        read -rp "  GIT_USER_NAME: " GIT_USER_NAME
        [ -z "$GIT_USER_NAME" ] && echo "  Name cannot be empty, try again."
    done
    MODIFIED=true
fi

if [ -z "${GIT_USER_EMAIL:-}" ]; then
    while [ -z "$GIT_USER_EMAIL" ]; do
        read -rp "  GIT_USER_EMAIL: " GIT_USER_EMAIL
        [ -z "$GIT_USER_EMAIL" ] && echo "  Email cannot be empty, try again."
    done
    MODIFIED=true
fi

if [ "$MODIFIED" = true ]; then
    cat > "$ENV_FILE" <<EOF
GITHUB_TOKEN='$GITHUB_TOKEN'
GIT_USER_NAME='$GIT_USER_NAME'
GIT_USER_EMAIL='$GIT_USER_EMAIL'
EOF
    chmod 600 "$ENV_FILE"
    echo "GitHub credentials saved to $ENV_FILE"
    echo "Edit that file before re-running to change values."
fi

GITHUB_AUTH=(-H "Authorization: token $GITHUB_TOKEN")

echo "Installing dependencies..."

# Install gnupg and curl early (needed for Brave repo key import)
sudo apt install -y gnupg curl

APT_PACKAGES=(
    sway waybar foot foot-terminfo fuzzel
    network-manager wl-clipboard copyq copyq-plugins
    glow brightnessctl wireplumber fonts-jetbrains-mono
    git python3 timeshift libglib2.0-bin
    nftables dnsmasq golang-go podman vlc iw rfkill
    openssl               # Required by seal -s for root password generation
    passwd                # Required by seal -s for chpasswd (set root password)
    nodejs npm
    eject exfatprogs ffmpeg
    gh                    # GitHub CLI (auth, credential helper)
    tcpdump ethtool       # Network diagnostics (WiFi recovery for seal/unseal)
    android-sdk-platform-tools  # ADB + fastboot for UAD-NG Android debloat CLI
    zip unzip             # Archive handling (.zip files)
    fzf ranger
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
# BACKLIGHT — systemd-backlight auto-detection (persists across reboots)
# =========================================================================

UNIT_PATH=""
[ -f /usr/lib/systemd/system/systemd-backlight@.service ] && UNIT_PATH=/usr/lib/systemd/system/systemd-backlight@.service
[ -f /lib/systemd/system/systemd-backlight@.service ] && UNIT_PATH=/lib/systemd/system/systemd-backlight@.service

if [ -z "$UNIT_PATH" ]; then
    echo "Backlight: systemd-backlight@.service not found — skipping"
else
    for dev in /sys/class/backlight/*; do
        [ -e "$dev" ] || continue
        dev_name=$(basename "$dev")
        service="systemd-backlight@backlight:$dev_name.service"
        if ! systemctl is-enabled "$service" &>/dev/null; then
            sudo systemctl enable "$service"
            echo "Backlight: $dev_name — systemd-backlight enabled"
        else
            echo "Backlight: $dev_name — systemd-backlight already enabled"
        fi
    done
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
# INTEL AX201 — WiFi stability
# =========================================================================

printf 'options iwlwifi power_save=0 uapsd_disable=1\noptions iwlmvm power_scheme=1\n' \
  | sudo tee /etc/modprobe.d/iwlwifi-opt.conf > /dev/null
sudo chmod 644 /etc/modprobe.d/iwlwifi-opt.conf

printf '[connection]\nwifi.powersave=2\n' \
  | sudo tee /etc/NetworkManager/conf.d/90-wifi-power-save.conf > /dev/null
sudo chmod 644 /etc/NetworkManager/conf.d/90-wifi-power-save.conf

echo "WiFi: AX201 power management disabled (modprobe + NM)"

# =========================================================================
# NOUVEAU GSP FIRMWARE (GPU power management — enables reclocking)
# =========================================================================

if [ ! -f /etc/default/grub.d/99-nouveau-gsp.cfg ] \
    || ! grep -q 'nouveau.config=NvGspRm=1' /etc/default/grub.d/99-nouveau-gsp.cfg 2>/dev/null; then
    sudo mkdir -p /etc/default/grub.d
    sudo cp .config/grub.d/99-nouveau-gsp.cfg /etc/default/grub.d/99-nouveau-gsp.cfg
    sudo chown root:root /etc/default/grub.d/99-nouveau-gsp.cfg
    sudo chmod 644 /etc/default/grub.d/99-nouveau-gsp.cfg
    sudo update-grub
    NEEDS_REBOOT=true
    echo "Nouveau GSP: kernel param added (reboot required)"
else
    echo "Nouveau GSP: already configured, skipping"
fi

# =========================================================================

# Write managed bashrc to dedicated file (overwritten each run)
cp .config/bashrc ~/.config/bashrc

# Source it from ~/.bashrc if not already present
if ! grep -q 'source ~/.config/bashrc' ~/.bashrc 2>/dev/null; then
    echo '[ -f ~/.config/bashrc ] && source ~/.config/bashrc' >> ~/.bashrc
    echo "bashrc: source ~/.config/bashrc added"
else
    echo "bashrc: source ~/.config/bashrc already present"
fi

source ~/.config/bashrc

# Generate default ranger config (creates ~/.config/ranger/)
if command -v ranger &>/dev/null && [ ! -d ~/.config/ranger ]; then
    ranger --copy-config=all >/dev/null 2>&1 || true
    echo "ranger: default config generated in ~/.config/ranger/"
fi

# =========================================================================
# WORK TOOLS
# =========================================================================

echo "Installing work tools..."

# Install localsend (not in Debian repos — download .deb from GitHub)
if ! dpkg -s localsend &>/dev/null 2>&1; then
    echo "Installing LocalSend..."
    LS_DEB=$(mktemp)
    LS_URL=$(curl -s "${GITHUB_AUTH[@]}" https://api.github.com/repos/localsend/localsend/releases/latest \
        | grep "browser_download_url.*linux-x86-64\.deb" \
        | cut -d '"' -f 4) || true
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
    OBS_URL=$(curl -s "${GITHUB_AUTH[@]}" https://api.github.com/repos/obsidianmd/obsidian-releases/releases/latest \
        | grep "browser_download_url.*amd64\.deb" \
        | cut -d '"' -f 4) || true
    if [ -n "$OBS_URL" ]; then
        curl -fsSL -o "$OBS_DEB" "$OBS_URL"
        sudo apt install -y libxss1
        sudo dpkg -i "$OBS_DEB"
        rm -f "$OBS_DEB"
    else
        echo "  WARNING: Could not determine latest Obsidian URL, skipping"
    fi
fi

# Remove Obsidian default vault if present (we use our own)
if [ -d "/home/mike/Obsidian Vault" ]; then
    rm -rf "/home/mike/Obsidian Vault"
    echo "Obsidian: removed default 'Obsidian Vault' (using knowledge_base instead)"
fi

# Copy Obsidian vault dark mode config
cp .config/obsidian/appearance.json "/home/mike/knowledge_base/.obsidian/appearance.json"
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

# Install yt-dlp (standalone binary from GitHub — latest version, not apt)
if ! command -v yt-dlp &>/dev/null; then
    echo "Installing yt-dlp..."
    YT_DLP=$(mktemp)
    YT_URL=$(curl -s "${GITHUB_AUTH[@]}" https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest \
        | grep "browser_download_url.*yt-dlp_linux\"" \
        | cut -d '"' -f 4) || true
    if [ -n "$YT_URL" ] && curl -fsSL -o "$YT_DLP" "$YT_URL"; then
        sudo cp "$YT_DLP" /usr/local/bin/yt-dlp
        sudo chmod 755 /usr/local/bin/yt-dlp
        rm -f "$YT_DLP"
        echo "yt-dlp installed to /usr/local/bin/yt-dlp"
    else
        echo "  WARNING: Could not install yt-dlp, skipping"
        rm -f "$YT_DLP"
    fi
fi

# =========================================================================
# UAD-NG CLI (Android debloat tool — requires Rust to build from source)
# =========================================================================

# Install Rust toolchain if missing
if ! command -v rustc &>/dev/null; then
    echo "Installing Rust toolchain..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
fi

# Build UAD-NG CLI (uad) from source
if ! command -v uad &>/dev/null; then
    echo "Building UAD-NG CLI from source..."
    BUILD_DIR=$(mktemp -d)
    git clone https://github.com/Universal-Debloater-Alliance/universal-android-debloater-next-generation "$BUILD_DIR"
    (cd "$BUILD_DIR" && cargo build --release -p uad-cli)
    sudo cp "$BUILD_DIR"/target/release/uad /usr/local/bin/uad
    sudo chmod 755 /usr/local/bin/uad
    rm -rf "$BUILD_DIR"
    echo "uad installed to /usr/local/bin/uad"
fi

# =========================================================================
# GITHUB SETUP — authenticate gh and configure git
# =========================================================================

echo "Setting up GitHub..."
echo "$GITHUB_TOKEN" | gh auth login --with-token
git config --global credential.helper "!gh auth git-credential"

git config --global user.name "$GIT_USER_NAME"
git config --global user.email "$GIT_USER_EMAIL"
echo "GitHub setup complete"

# Copy configs to their system locations
mkdir -p ~/.config/sway
mkdir -p ~/.config/waybar
mkdir -p ~/.config/foot
mkdir -p ~/.config/fuzzel
mkdir -p ~/.config/copyq/themes
mkdir -p ~/.config/waybar/scripts
mkdir -p ~/.config/scripts
# Ensure seal_lib.py symlink for Ruff peer import resolution
# The symlink points from .config/scripts/ → ../allowlist/scripts/seal_lib.py
# It is a dev-time artifact only — never deployed. install.sh verifies
# it exists and points to the right target so Ruff stays clean.
TARGET="../allowlist/scripts/seal_lib.py"
LINK=".config/scripts/seal_lib.py"
if [ ! -L "$LINK" ] || [ "$(readlink "$LINK")" != "$TARGET" ]; then
    rm -f "$LINK"
    ln -s "$TARGET" "$LINK"
    echo "seal_lib.py symlink recreated"
fi
mkdir -p ~/.config/seal
touch ~/.config/seal/system.credentials ~/.config/seal/mobile.credentials
chmod 600 ~/.config/seal/system.credentials ~/.config/seal/mobile.credentials
mkdir -p ~/.config/zed
mkdir -p ~/.config/opencode

# Deploy utility scripts globally (see docs/utility-scripts.md)
for script in .config/scripts/*.sh; do
    name=$(basename "$script" .sh)
    sudo cp "$script" /usr/local/bin/"$name"
    sudo chmod 755 /usr/local/bin/"$name"
    echo "$name deployed to /usr/local/bin/$name"
done

# Deploy seal_lib.py alongside unseal (peer import at runtime)
sudo cp .config/allowlist/scripts/seal_lib.py /usr/local/bin/seal_lib.py
sudo chmod 644 /usr/local/bin/seal_lib.py

# Deploy unseal — mike-owned so user can run without sudo
sudo cp .config/scripts/unseal.py /usr/local/bin/unseal
sudo chmod 755 /usr/local/bin/unseal
sudo chown mike:mike /usr/local/bin/unseal
echo "unseal deployed to /usr/local/bin/unseal"

# Deploy sem — mike-owned mobile seal (no sudo needed)
sudo cp .config/scripts/sem.py /usr/local/bin/sem
sudo chmod 755 /usr/local/bin/sem
sudo chown mike:mike /usr/local/bin/sem
echo "sem deployed to /usr/local/bin/sem"

cp .config/sway/sway_config ~/.config/sway/config
cp .config/waybar/waybar_config.json ~/.config/waybar/config.json
cp .config/waybar/style.css ~/.config/waybar/style.css
cp .config/waybar/mocha.css ~/.config/waybar/mocha.css
cp .config/foot/foot.ini ~/.config/foot/foot.ini
cp .config/fuzzel/fuzzel.ini ~/.config/fuzzel/fuzzel.ini
cp .config/copyq/copyq.conf ~/.config/copyq/copyq.conf
cp .config/copyq/themes/* ~/.config/copyq/themes/
cp .config/waybar/scripts/* ~/.config/waybar/scripts/
cp .config/zed/settings.json ~/.config/zed/settings.json
cp .config/opencode/opencode.jsonc ~/.config/opencode/opencode.jsonc

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
sudo cp .config/allowlist/scripts/*.sh .config/allowlist/scripts/*.py /opt/allowlist/
sudo mkdir -p .config/allowlist/domains
for AWL in .config/allowlist/domains/*.txt; do
    base=$(basename "$AWL")
    [ "$base" = "deny.txt" ] && continue
    sudo cp "$AWL" /opt/allowlist/allowlist."$base"
done
sudo cp .config/allowlist/domains/deny.txt /opt/allowlist/deny.txt
sudo cp .config/nftables/nftables.conf.base /opt/allowlist/
sudo cp .config/nftables/nftables.conf.locked /opt/allowlist/
sudo cp .config/brave/policy.json.template /opt/allowlist/brave-policy.json.template
sudo cp .config/firefox/policies.json.template /opt/allowlist/firefox-policies.json.template
sudo chown -R root:root /opt/allowlist
sudo chmod 750 /opt/allowlist
sudo chmod 750 /opt/allowlist/allowlist.sh
sudo chmod 750 /opt/allowlist/generate-dnsmasq.sh
sudo chmod 750 /opt/allowlist/generate-policies.sh
sudo chmod 750 /opt/allowlist/generate-nftables.sh
sudo chmod 750 /opt/allowlist/verify.sh
sudo chmod 750 /opt/allowlist/seal.py
sudo chmod 640 /opt/allowlist/allowlist.infra.txt
sudo chmod 640 /opt/allowlist/allowlist.base.txt
sudo chmod 640 /opt/allowlist/allowlist.session.txt
sudo chmod 640 /opt/allowlist/deny.txt
sudo chmod 640 /opt/allowlist/brave-policy.json.template
sudo chmod 640 /opt/allowlist/firefox-policies.json.template
sudo chmod 640 /opt/allowlist/nftables.conf.base
sudo chmod 640 /opt/allowlist/nftables.conf.locked

echo "Allowlist utility deployed to /opt/allowlist/"

# Deploy browser policies from template (Brave, Chrome, Chromium, Firefox)
sudo /opt/allowlist/generate-policies.sh

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
# SUDOERS — restricted commands for mike (no full sudo)
# =========================================================================

sudo mkdir -p /etc/sudoers.d
sudo cp .config/sudoers/99-mike-tools /etc/sudoers.d/99-mike-tools
sudo chown root:root /etc/sudoers.d/99-mike-tools
sudo chmod 440 /etc/sudoers.d/99-mike-tools

# =========================================================================
# POST-INSTALL MANUAL STEPS SIGNAL
# =========================================================================

echo ""
tput bold 2>/dev/null && tput setaf 1 2>/dev/null
echo "================================================"
echo "  SUDOERS: mike granted restricted sudo (apt/systemctl/journalctl)"
echo "================================================"
tput sgr0 2>/dev/null
echo ""
echo "  Allowed: apt update|upgrade|install --reinstall,"
echo "  systemctl status/*, journalctl"
echo "  Run manually: sudo gpasswd -d mike sudo"
echo "  See docs/root_ownership_inventory.md"
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

# =========================================================================
# REBOOT PROMPT (only if critical kernel/cfg changes were made)
# =========================================================================

if [ "$NEEDS_REBOOT" = true ]; then
    echo ""
    echo "The following changes require a reboot to take effect:"
    echo "  - Nouveau GSP firmware (GPU power management)"
    echo "  - Kernel cmdline parameters"
    echo ""
    read -p "Reboot now? (y/N): " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        sudo systemctl reboot
    else
        echo "Remember to reboot later for changes to take effect."
    fi
fi
