# NVIDIA Proprietary Driver — Dual-GPU Setup

## Hardware

- **Intel UHD Graphics (Raptor Lake-S)** — PCI 00:02.0, DRM driver i915
- **NVIDIA RTX 3050 6GB Laptop GPU** — PCI 01:00.0, DRM driver nouveau
- **Laptop panel (eDP-1)** — physically wired to Intel only
- **HDMI-A-1** — wired to NVIDIA (external monitor port)

## Scope

This configuration assumes exactly two conditions:

1. **OS + Compositor**: Debian netinstall with Sway on wlroots. Not tested
   on Fedora, Ubuntu, Arch, or under GNOME/KDE. Tools like `update-initramfs`,
   `apt-get`, and `WLR_DRM_DEVICES` are Debian/sway-specific.

2. **Hardware**: Intel iGPU (wired to laptop panel) + single NVIDIA dGPU.
   Not designed for AMD iGPU, single-GPU systems, or multi-NVIDIA setups.

These are not limitations to be fixed — they are the foundation this entire
setup is built on.

## The Problem

Nouveau on Ampere (RTX 3050) lacks CUDA, NVENC, PRIME render offload, and
proper power management. The GSP firmware param (`NvGspRm=1`) helps with
power but does not fix the core limitations. This causes:

- No CUDA for AI/ML workloads
- No NVENC for hardware video encoding
- No PRIME render offload for GPU-intensive apps
- Sway input lag from nouveau DRM log spam

## Why Other Approaches Do Not Work

### Approach 1: nvidia-drm.modeset=1 + sway --unsupported-gpu

This is the standard approach (justaguylinux, Arch wiki, Hyprland wiki).
It makes sway render on the NVIDIA GPU directly.

**Does not work here because:**

The laptop display (eDP-1) is hardwired to the Intel GPU. When nvidia-drm
loads with modeset=1, it creates a second DRM primary node. wlroots 0.18
cannot handle dual-GPU modesetting — it crashes with DRM errors.

This was already attempted and documented in `archive/docs/nvidia-attempt.md`.
The system was unrecoverable and required Timeshift restore.

### Approach 2: WLR_DRM_DEVICES to force Intel

Setting `WLR_DRM_DEVICES=/dev/dri/by-path/pci-0000:00:02.0-card` tells
wlroots to only use the Intel GPU. This works for sway stability but
does not install the nvidia driver — so CUDA/NVENC remain unavailable.

### Approach 3: Nouveau with GSP firmware

The current setup. Adds `nouveau.config=NvGspRm=1` to GRUB. Improves
power management but does not enable CUDA, NVENC, or PRIME offload.
Nouveau on Ampere simply does not support these features.

## Installation

The NVIDIA CUDA network repo provides compute-only packages without
modifying Debian's system repos:

1. `cuda-keyring` — `.deb` package that adds NVIDIA's GPG key + apt source
2. `nvidia-driver-cuda` — compute-only userspace (nvidia-smi, libcuda, NVENC)
   No X11/GL/EGL/Wayland dependencies
3. `nvidia-kernel-open-dkms` — open kernel modules (Turing+/Ampere recommended)
   Built via DKMS, auto-rebuilds on kernel updates

The installer (`lib/35-nvidia.sh`) handles all of this: downloads the
keyring, runs `dpkg -i`, `apt update`, then `apt install`. No manual
steps needed.

## Why Our Approach Works

**Compute-only design.** Load only `nvidia.ko` (core) and `nvidia-uvm.ko`
(CUDA) from the open kernel modules (`nvidia-kernel-open-dkms`). Block
`nvidia-drm.ko` and `nvidia-modeset.ko` at modprobe level so they never
register a DRM node.

Result:
- Sway sees only Intel GPU (i915) — identical to current working setup
- nvidia.ko loaded — CUDA, NVENC available
- No DRM node conflict — sway does not crash

This works because:
1. The display is wired to Intel — Intel MUST handle display output
2. CUDA/NVENC use the nvidia driver directly (not DRM) — they work
   without nvidia-drm
3. PRIME render offload does NOT work — it requires nvidia-drm
   (see "Why Gaming on NVIDIA Is Impossible" below)

## Use Cases — How NVIDIA Is Triggered

### Automatic (no user action needed)

These apps detect and use NVIDIA through the CUDA/NVENC driver API,
which does not go through DRM/KMS:

| Use Case | App | Mechanism |
|----------|-----|-----------|
| AI/ML inference | ollama, llama.cpp, PyTorch | CUDA runtime detects nvidia.ko |
| 3D rendering | Blender (GPU render) | CUDA/OptiX in preferences |
| Video editing | DaVinci Resolve | CUDA in preferences |
| Video encoding | ffmpeg -c:v h264_nvenc | NVENC SDK |
| CUDA compute | Any CUDA app | CUDA driver API |

### Not affected (display-only apps)

These apps use Intel for display and do not need NVIDIA:
- Web browsers (Firefox, Chrome)
- Terminal emulators (kitty, foot)
- File managers (Thunar)
- All sway/wayland native apps

## Why Gaming on NVIDIA Is Impossible

PRIME render offload requires `nvidia-drm.ko` to be loaded. The NVIDIA
official README states:

> "Ensure that the nvidia-drm kernel module is loaded. This should
> normally happen by default, but you can confirm by running
> `lsmod | grep nvidia-drm` to see if the kernel module is loaded.
> Run `modprobe nvidia-drm` to load it."
>
> — NVIDIA PRIME Render Offload README (drivers 435.21 through 555.42.02)

Since our compute-only approach blocks `nvidia-drm`, the environment
variable `__NV_PRIME_RENDER_OFFLOAD=1` is ignored. The NVIDIA GPU
cannot render frames for display.

This affects all graphics-rendered applications:

| What | Why it fails | Example |
|------|-------------|---------|
| Wine/Proton games | DXVK translates DX to Vulkan, but Vulkan WSI needs nvidia-drm for frame presentation | GTA4, Resident Evil, God of War |
| Console emulators (Vulkan) | Same Vulkan WSI limitation | RPCS3 (PS3), shadPS4 (PS4), PCSX2 (PS2) |
| Any prime-run app | `__NV_PRIME_RENDER_OFFLOAD=1` is a no-op without nvidia-drm | vkcube, glxgears |

### The rendering chain

```
Game (DX11/12)
    ↓
DXVK (translates to Vulkan)
    ↓
NVIDIA Vulkan driver (loads via nvidia.ko — works)
    ↓
Vulkan WSI needs nvidia-drm for frame presentation
    ↓
nvidia-drm is BLOCKED → chain breaks → no output
```

### What CAN run on Intel UHD 770

The Intel iGPU can handle lighter workloads:
- PS1 emulation (DuckStation) — lightweight, 720p fine
- PSP emulation (PPSSPP) — lightweight, 720p fine
- PS2 emulation (PCSX2) — borderline, some titles at native 720p
- Older PC games via Wine/Proton — 720p low settings possible
- PS3/PS4 emulation — requires discrete GPU, won't work on iGPU

## Architecture Summary

`nvidia.ko` binds to the GPU at boot via `/etc/modules-load.d/nvidia-compute.conf`.
The install script (`lib/35-nvidia.sh`) deploys configs and builds kernel modules
via DKMS, but does not load them into the running kernel. A reboot is required
after installation.

```
┌─────────────────────────────────────────────┐
│                  Sway (WM)                   │
│              Renders on Intel                │
│           DRM: card1 (i915) only            │
├─────────────────────────────────────────────┤
│  Display output: Intel eDP-1 → Laptop screen │
├─────────────────────────────────────────────┤
│              NVIDIA GPU (RTX 3050)           │
│  Open kernel modules (nvidia-kernel-open)   │
│  nvidia.ko loaded, nvidia-drm BLOCKED       │
│  Available for: CUDA, NVENC                  │
│  NOT registered as DRM device               │
│  Source: NVIDIA CUDA network repo            │
└─────────────────────────────────────────────┘
```
