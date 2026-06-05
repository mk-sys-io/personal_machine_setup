#!/bin/bash

set -euo pipefail

cd linux_setupfi

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
cp .config/foot.ini ~/.config/foot/foot.ini
cp .config/wofi-config ~/.config/wofi/config
cp .config/wofi-style.css ~/.config/wofi/style.css
cp .config/scripts/* ~/.config/waybar/scripts/
# Restore original Debian .bashrc from /etc/skel (overwrites any previous customization)
cp /etc/skel/.bashrc ~/.bashrc
echo "Restored original Debian .bashrc from /etc/skel/.bashrc"

# Append custom bashrc additions (idempotent — guarded by marker)
if ! grep -q "# linux_setup additions" ~/.bashrc 2>/dev/null; then
cat >> ~/.bashrc << 'EOF'

# --- linux_setup additions ---
# User specific environment
if ! [[ "$PATH" =~ "$HOME/.local/bin:$HOME/bin:" ]]; then
    PATH="$HOME/.local/bin:$HOME/bin:$PATH"
fi
export PATH

# User specific aliases and functions
if [ -d ~/.bashrc.d ]; then
    for rc in ~/.bashrc.d/*; do
        if [ -f "$rc" ]; then
            . "$rc"
        fi
    done
fi
unset rc
umask 002

alias help="grep -E '^[[:space:]]*bindsym' ~/.config/sway/config | sed 's/.*bindsym //' | awk '{printf \"%-30s %s\\n\", \$1, substr(\$0, index(\$0,\$2))}' | less -R"

if [ -z "${DISPLAY}" ] && [ -z "${WAYLAND_DISPLAY}" ] && [ "${XDG_VTNR}" -eq 1 ]; then
    exec sway
fi
EOF
    echo "Appended custom bashrc additions"
else
    echo "Custom bashrc additions already present, skipping"
fi
