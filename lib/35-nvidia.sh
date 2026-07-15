#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# 35-nvidia.sh — NVIDIA proprietary driver (compute-only)
#
# Prerequisites:
#   - Debian netinstall (apt, dpkg, update-initramfs)
#   - Sway on wlroots (WLR_DRM_DEVICES)
#   - Dual-GPU: Intel iGPU (wired to laptop panel) + single NVIDIA dGPU
#   - NOT supported: AMD iGPU, single GPU, multi-NVIDIA, other distros/DEs
#
# Bootstraps the NVIDIA CUDA repo, installs nvidia-driver-cuda +
# nvidia-kernel-open-dkms, deploys modprobe/modules-load configs,
# and rebuilds initramfs. Sway stays on Intel via WLR_DRM_DEVICES.
# nvidia-drm and nvidia-modeset are blocked — no DRM node conflict.
#
# nvidia.ko binds to the GPU at boot (via modules-load.d), not runtime.
# This script only deploys configs and builds modules — a reboot is required.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# ---------------------------------------------------------------------------
# Detect: need NVIDIA GPU + iGPU
# ---------------------------------------------------------------------------

if ! lspci -nn | grep -qiE '0300.*10de'; then
    log "NVIDIA: no GPU found — skipping"
    exit 0
fi

if ! lspci -nn | grep -qiE '0300.*8086'; then
    log_warn "NVIDIA: GPU found but no Intel iGPU — skipping (dual-GPU with Intel required)"
    exit 0
fi

log_step "NVIDIA configuration"

# ---------------------------------------------------------------------------
# Update apt cache (must happen before any package operations)
# ---------------------------------------------------------------------------

sudo apt-get update -qq
log_ok "APT cache updated"

# ---------------------------------------------------------------------------
# Sanity: kernel headers must be available for DKMS build
# ---------------------------------------------------------------------------

headers_pkg="linux-headers-$(uname -r)"
if ! pkg_installed "$headers_pkg"; then
    if ! apt-cache show "$headers_pkg" >/dev/null 2>&1; then
        log_error "NVIDIA: $headers_pkg not available — cannot build kernel modules"
        exit 1
    fi
    log "Installing kernel headers for running kernel..."
    sudo apt-get install -y "$headers_pkg"
fi
log_ok "Kernel headers installed: $headers_pkg"

# ---------------------------------------------------------------------------
# Sanity: dkms must be installed (from packages/apt.txt)
# ---------------------------------------------------------------------------

if ! pkg_installed dkms; then
    log_error "NVIDIA: dkms not installed — required for kernel module builds"
    exit 1
fi
log_ok "DKMS installed"

# ---------------------------------------------------------------------------
# Bootstrap: install NVIDIA CUDA repo keyring
# ---------------------------------------------------------------------------

CUDA_CODENAME=$(source /etc/os-release && echo "$VERSION_CODENAME")
CUDA_ARCH=$(dpkg --print-architecture)
CUDA_REPO_URL="https://developer.download.nvidia.com/compute/cuda/repos/${CUDA_CODENAME}/${CUDA_ARCH}"

CUDA_KEYRING_DEB=$(curl -fsSL --connect-timeout "$CURL_TIMEOUT_CONNECT" --max-time "$CURL_TIMEOUT_DOWNLOAD" \
    "$CUDA_REPO_URL/" 2>/dev/null | grep -oP 'cuda-keyring_[^"<>]+\.deb' | sort -V | tail -1)

if [[ -z "$CUDA_KEYRING_DEB" ]]; then
    log_error "NVIDIA: failed to fetch latest keyring filename from ${CUDA_REPO_URL}"
    exit 1
fi

CUDA_KEYRING_URL="${CUDA_REPO_URL}/${CUDA_KEYRING_DEB}"

if ! dpkg -s cuda-keyring >/dev/null 2>&1; then
    log "Downloading NVIDIA CUDA keyring: ${CUDA_KEYRING_DEB}..."
    tmp_deb="$(mktemp --suffix=.deb)"
    trap 'rm -f "$tmp_deb"' RETURN

    if curl -fsSL --connect-timeout "$CURL_TIMEOUT_CONNECT" --max-time "$CURL_TIMEOUT_DOWNLOAD" \
         -o "$tmp_deb" "$CUDA_KEYRING_URL"; then
        sudo dpkg -i "$tmp_deb"
        log_ok "NVIDIA CUDA keyring installed"
    else
        log_error "NVIDIA: failed to download CUDA keyring"
        exit 1
    fi
else
    log_ok "NVIDIA CUDA keyring already installed"
fi

# ---------------------------------------------------------------------------
# Install NVIDIA compute driver + open kernel modules
# ---------------------------------------------------------------------------

nvidia_pkgs=(nvidia-driver-cuda nvidia-kernel-open-dkms)
all_installed=true
for pkg in "${nvidia_pkgs[@]}"; do
    if ! pkg_installed "$pkg"; then
        all_installed=false
        break
    fi
done

if [[ "$all_installed" == true ]]; then
    log_ok "NVIDIA packages already installed: ${nvidia_pkgs[*]}"
else
    log "Installing NVIDIA compute driver and kernel modules..."
    sudo apt-get install -y "${nvidia_pkgs[@]}"
    log_ok "NVIDIA packages installed: ${nvidia_pkgs[*]}"
fi

# ---------------------------------------------------------------------------
# Build nvidia modules for running kernel via DKMS
# ---------------------------------------------------------------------------

if ! sudo dkms autoinstall; then
    log_error "NVIDIA: DKMS build failed"
    log_error "  dkms status:"
    dkms status 2>&1 | while IFS= read -r line; do log_error "    $line"; done
    exit 1
fi
log_ok "DKMS modules built for $(uname -r)"

# ---------------------------------------------------------------------------
# Verify kernel module exists
# ---------------------------------------------------------------------------

if ! find "/lib/modules/$(uname -r)" -name 'nvidia.ko*' | grep -q .; then
    log_error "NVIDIA: nvidia.ko not found — DKMS build may have failed"
    log_error "  Check: dkms status"
    exit 1
fi
log_ok "Kernel module verified: nvidia.ko"

# ---------------------------------------------------------------------------
# Deploy modprobe configs
# ---------------------------------------------------------------------------

sudo mkdir -p /etc/modprobe.d
sudo cp "$REPO_ROOT/system/modprobe.d/z99-nvidia-drm-block.conf"     /etc/modprobe.d/
sudo cp "$REPO_ROOT/system/modprobe.d/nvidia-blacklist-nouveau.conf" /etc/modprobe.d/
log_ok "Modprobe configs deployed"

# ---------------------------------------------------------------------------
# Deploy modules-load
# ---------------------------------------------------------------------------

sudo mkdir -p /etc/modules-load.d
sudo cp "$REPO_ROOT/system/modules-load.d/nvidia-compute.conf" /etc/modules-load.d/
log_ok "Modules-load config deployed"

# ---------------------------------------------------------------------------
# Detect Intel GPU PCI address for WLR_DRM_DEVICES
# ---------------------------------------------------------------------------

intel_pci=$(lspci -nn -d 8086: | grep -iE 'VGA|Display' | awk '{print $1}')
if [[ -z "$intel_pci" ]]; then
    log_error "NVIDIA: cannot find Intel GPU PCI address"
    exit 1
fi

# Use by-path symlink — stable across reboots (cardN numbers can shift)
intel_dri="/dev/dri/by-path/pci-${intel_pci}-card"
if [[ ! -c "$intel_dri" ]]; then
    log_error "NVIDIA: ${intel_dri} not found"
    exit 1
fi
log_ok "Intel GPU detected: ${intel_pci} → ${intel_dri}"

# ---------------------------------------------------------------------------
# Force sway to use Intel GPU via WLR_DRM_DEVICES
# ---------------------------------------------------------------------------

profile="$REAL_HOME/.profile"
wlr_line="export WLR_DRM_DEVICES=${intel_dri}"
if grep -qF "WLR_DRM_DEVICES" "$profile" 2>/dev/null; then
    # Update existing line in case it points to old cardN path
    sed -i "s|^export WLR_DRM_DEVICES=.*|${wlr_line}|" "$profile"
    log_ok "WLR_DRM_DEVICES updated in ~/.profile"
else
    printf '\n# Force Intel GPU for wlroots/sway (added by linux_setup)\n%s\n' "$wlr_line" >> "$profile"
    log_ok "WLR_DRM_DEVICES set to ${intel_dri} in ~/.profile"
fi

# ---------------------------------------------------------------------------
# Rebuild initramfs
# ---------------------------------------------------------------------------

sudo update-initramfs -u
log_ok "Initramfs rebuilt"

needs_reboot

log_step "NVIDIA complete"
exit 0
