# NVIDIA Proprietary Driver — Attempted Migration (Failed)

## Background

Issue 12 from the tracker: nouveau on RTX 3050 (Ampere) lacks CUDA, NVENC,
PRIME offload, and proper power management.

## Approach

Path A — Replace nouveau with the proprietary `nvidia-driver` metapackage.
Implemented entirely in `install.sh` and supporting config files.

## Changes Made

### install.sh (major rewrite)
- Replaced nouveau GSP block with full NVIDIA proprietary driver section
- Added `contrib` + `non-free` apt repo detection (needed for nvidia deps)
- Consolidated Brave/Chrome repo additions with single `apt update`
- Blacklists nouveau (`/etc/modprobe.d/nvidia-blacklist.conf`)
- Adds `nvidia_drm.modeset=1` kernel parameter
- Installs `nvidia-driver nvidia-smi` via apt
- Deploys `prime-run` wrapper (`/usr/local/bin/prime-run`)
- Builds nvidia DKMS module for ALL installed kernels
- Rebuilds initramfs for all kernels (`update-initramfs -u -k all`)

### .config/bashrc
- Sets `WLR_DRM_DEVICES=/dev/dri/by-path/pci-0000:00:02.0-card` to force
  wlroots to use Intel iGPU for display (avoid nvidia-drm conflict)

### .config/grub.d/99-nouveau-gsp.cfg (deleted)
- Removed; replaced by `99-nvidia-modeset.cfg` deployed by install.sh

### .config/sudoers/99-mike-tools
- Added `prime-select` NOPASSWD entry

### manual_work.md
- Replaced nouveau GSP verification with nvidia-smi / dmesg checks
- Added boot failure troubleshooting section (TTY fallback, DKMS logs)

## Failure

After reboot into kernel 6.12.90, Sway crashes immediately with DRM errors.
The system was dual-GPU (Intel UHD Graphics + NVIDIA RTX 3050 Laptop GPU).
Despite setting `WLR_DRM_DEVICES` to force the Intel GPU, sway/wlroots
failed to initialize on the new kernel. The old kernel (6.12.86) also broke
once nvidia modules were built for it. System was unrecoverable via TTY
and required a Timeshift snapshot to restore.

## Root Cause (likely)

`nvidia_drm.modeset=1` exposes nvidia-drm as a second DRM device. On this
laptop the display is wired to the Intel iGPU only. wlroots 0.18 cannot
handle this dual-GPU modesetting configuration gracefully. The
`WLR_DRM_DEVICES` workaround was insufficient — likely because nvidia-drm
registering as a DRM primary node still interfered with device enumeration.

## Conclusion

NVIDIA proprietary driver is not stable on this dual-GPU laptop with Sway.
Reverted to nouveau GSP via Timeshift restore. No further attempts planned.

## Related Files

- `install.sh` — NVIDIA install section (reverted to nouveau GSP block below)
- `manual_work.md` — verification steps (reverted)
- `.config/bashrc` — `WLR_DRM_DEVICES` line (removed)
- `.config/sudoers/99-mike-tools` — `prime-select` entry (kept — harmless)
