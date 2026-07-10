# NVIDIA Proprietary Driver — Compute-Only Setup

## Status

Replaces the failed monolithic approach documented in `nvidia-attempt.md`.
Runs as an automated section in `install.sh` with a Timeshift snapshot
prompt at the very start.

## Lessons from Previous Failure

The monolithic attempt (`nvidia-attempt.md`) crashed Sway irrecoverably and
required Timeshift restore. Four root causes, all addressed here:

| # | Failure | Cause | Mitigation in this plan |
|---|---------|-------|------------------------|
| 1 | **nvidia-drm DRM node conflict** | `nvidia_drm.modeset=1` exposed nvidia-drm as a second DRM primary node. wlroots 0.18 can't handle dual-GPU modesetting. `WLR_DRM_DEVICES` was insufficient | `install nvidia-drm /bin/true` and `install nvidia-modeset /bin/true` in modprobe.d — these modules **never load**, no DRM node is ever created |
| 2 | **DKMS infects all kernels** | `nvidia-kernel-dkms` builds modules for every installed kernel. A previously-working kernel (6.12.86) broke once nvidia modules were built for it | Modprobe block applies to all kernels uniformly — even if nvidia-drm.ko exists on disk, it can never be loaded on any kernel |
| 3 | **No incremental checkpoint** | Everything installed in one shot (driver + modprobe + initramfs + reboot). No way to isolate which step caused the failure | Timeshift snapshot **before** any changes is mandatory — `install.sh` prompts for it at the start. If reboot fails, `timeshift restore` |
| 4 | **nvidia-driver metapackage** | `nvidia-driver` pulls in settings GUI, display manager integration, etc. Unnecessary attack surface for a compute-only setup | Minimal install: `nvidia-kernel-dkms nvidia-utils` only |

## Design

**Compute-only.** Only `nvidia.ko` (core) and `nvidia-uvm.ko` (CUDA) load at boot.
`nvidia-drm.ko` and `nvidia-modeset.ko` are blocked at modprobe.d level — they
never register a DRM node. wlroots/sway only see the Intel iGPU, identical to
the current working setup. CUDA, NVENC, and PRIME render offload work through
the nvidia core driver without display involvement.

**Automated deployment.** The entire driver setup is one section in `install.sh`,
gated by GPU detection (`lspci | grep '0300.*10de'`). If no NVIDIA GPU is
present, the existing nouveau GSP logic is preserved unchanged.

## Phases

### Phase 0 — Timeshift snapshot (automated in install.sh)

**Goal:** Recoverable baseline before any system change.

Steps:
1. `install.sh` prompts at the top (after sudo keepalive, before any apt or
   config changes):
   ```
   ============================================
     IMPORTANT: Create a Timeshift snapshot
   ============================================

     Before proceeding, create a snapshot:
       sudo timeshift --create --comments "before-install"

   Ready to proceed? (y/N):
   ```
2. User creates snapshot in a separate terminal, then confirms.
3. Any answer other than `y`/`Y` exits immediately — no changes made.

**Rollback:** `sudo timeshift --restore` to the snapshot.

---

### Phase 1 — Automated deployment (install.sh)

**Goal:** All packages, configs, and initramfs changes applied. Sway still
works (no reboot yet).

Sequential steps within the script:

1. **Detect GPU**
   ```
   lspci -nn | grep -qiE '0300.*10de'
   ```
   If false, skip to the existing nouveau GSP fallback.

2. **Enable non-free repos** (if not already present)
   - Detect mirror and suite from `/etc/apt/sources.list`
   - Write a dedicated `.list` file:
     ```
     /etc/apt/sources.list.d/nvidia-non-free.list
     deb <mirror> <suite> main contrib non-free non-free-firmware
     ```
   - `sudo apt update`

3. **Deploy display module block** BEFORE package install
   - Source: `.config/modprobe.d/z99-nvidia-drm-block.conf`
   - Deployed to: `/etc/modprobe.d/z99-nvidia-drm-block.conf`
   - Content:
     ```
     install nvidia-drm /bin/true
     install nvidia-modeset /bin/true
     ```
   - The `install /bin/true` directive replaces the module's load command with
     a no-op, taking precedence over any modprobe config the package may ship.

4. **Sanity check — block works**
   ```
   sudo modprobe nvidia-drm          # should silently no-op
   lsmod | grep nvidia               # should be empty
   ```
   If either fails, the script exits with an error before touching the system.

5. **Install packages**
   ```
   sudo apt install -y nvidia-kernel-dkms nvidia-utils
   ```
   - `nvidia-kernel-dkms` builds `nvidia.ko`, `nvidia-uvm.ko`,
     `nvidia-drm.ko`, `nvidia-modeset.ko` via DKMS for all kernels.
   - `nvidia-utils` provides `nvidia-smi` and the udev rule for
     `/dev/nvidia-uvm` device node creation.
   - No metapackage (`nvidia-driver` pulls in settings GUI, display manager
     integration, etc.).

6. **Verify DKMS built modules**
   ```
   ls /lib/modules/$(uname -r)/updates/dkms/nvidia*.ko
   ```
   Expect 4 files (the two we block will be present on disk but never load).

7. **Re-verify block still works after install**
   ```
   sudo modprobe nvidia-drm          # still silent no-op
   ```

8. **Blacklist nouveau**
   - Source: `.config/modprobe.d/nvidia-blacklist-nouveau.conf`
   - Deployed to: `/etc/modprobe.d/nvidia-blacklist-nouveau.conf`
   - Content:
     ```
     blacklist nouveau
     ```

9. **Deploy boot-time module load**
   - Source: `.config/modules-load.d/nvidia-compute.conf`
   - Deployed to: `/etc/modules-load.d/nvidia-compute.conf`
   - Content:
     ```
     nvidia
     nvidia-uvm
     ```

10. **Remove stale nouveau GSP GRUB config**
    ```
    sudo rm -f /etc/default/grub.d/99-nouveau-gsp.cfg
    sudo update-grub
    ```

11. **Rebuild initramfs** (current kernel only)
    ```
    sudo update-initramfs -u
    ```
    Old kernels retain their initramfs without nvidia modules — fine, because
    modprobe block covers all kernels, and after rootfs mounts,
    `/lib/modules` provides the modules. Only the current kernel is relevant
    for the first reboot.

12. **Set NEEDS_REBOOT=true** and add nvidia to the reboot prompt.

**No reboot during script execution.** The first reboot happens when the user
responds to the prompt at the end. If Sway fails after reboot, Timeshift
restore returns to the Phase 0 snapshot.

**Rollback:** `sudo apt purge nvidia-kernel-dkms nvidia-utils` + remove
modprobe/modules-load configs + `update-initramfs -u` + `update-grub`
(restores grub.cfg without nouveau GSP — harmless, the param is unused).
Or simply `timeshift restore`.

---

### Phase 2 — First reboot & verification

**Goal:** Sway on Intel, nvidia-smi works, no nvidia DRM node.

Steps:
1. Reboot (initiated by user from the install.sh prompt).
2. On the other side:
   ```
   nvidia-smi                     # RTX 3050, driver version, VRAM
   ls /dev/nvidia*                # nvidia0, nvidiactl, nvidia-uvm
   ls /dev/dri/                   # only Intel cards (card0/card1), no new card
   cat /sys/class/drm/card*/device/uevent | grep DRIVER  # only i915
   ```
3. If Sway crashes at boot:
   - Switch to TTY (Ctrl+Alt+F2)
   - `sudo timeshift --restore` to the Phase 0 snapshot
   - Investigate `dmesg` for the cause (should not happen — nvidia-drm never
     loads, no DRM node is created)

**No nvidia-drm DRM node will appear.** The modprobe block is in effect at
the initramfs stage and persists through boot. `/dev/dri/` is identical to
the pre-nvidia state.

**Post-reboot device check:**
- `/dev/nvidia0` — created by nvidia.ko PCI probe
- `/dev/nvidiactl` — control device
- `/dev/nvidia-uvm` — created by the udev rule shipped with `nvidia-utils`
  (not by the module itself; confirm it exists)

**Rollback:** `timeshift restore` (fully recoverable to pre-install state).

---

### Phase 3 — Workload testing

**Goal:** Confirm CUDA, NVENC, and PRIME offload work.

No system changes — purely user-space verification.

1. **CUDA compute**
   ```
   nvidia-smi
   ```
   Or run `ollama` / llama.cpp with GPU offloading.

2. **NVENC encode**
   ```
   ffmpeg -encoders | grep nvenc       # h264_nvenc, hevc_nvenc
   ffmpeg -i input.mp4 -c:v h264_nvenc -preset p4 output.mp4
   ```

3. **PRIME render offload**
   ```
   __NV_PRIME_RENDER_OFFLOAD=1 glxinfo | grep "OpenGL renderer"
   ```
   Expected: `NVIDIA Corporation GA107BM [...] GeForce RTX 3050 ...`
   Without the env var:
   ```
   glxinfo | grep "OpenGL renderer"    # Intel UHD Graphics
   ```

4. **prime-run wrapper**
   - Source: `.config/scripts/prime-run.sh`
   - Deployed to `/usr/local/bin/prime-run` by the existing `*.sh` loop
     in `install.sh`
   - Usage: `prime-run blender`
   - Content:
     ```bash
     #!/bin/bash
     set -euo pipefail
     exec __NV_PRIME_RENDER_OFFLOAD=1 "$@"
     ```

**Rollback:** No rollback needed — no system changes in this phase.

---

## Files Created or Modified

| File | Type | Phase | Purpose |
|------|------|-------|---------|
| `docs/nvidia-setup.md` | Updated | 0 | This implementation plan |
| `docs/nvidia-attempt.md` | Updated | 0 | Superseded note at top |
| `docs/decisions.md` | Updated | 0 | ADR for compute-only design |
| `docs/user_reference.md` | Updated | 3 | `prime-run`, CUDA verification |
| `docs/utility-scripts.md` | Updated | 3 | NVENC examples |
| `.config/modprobe.d/z99-nvidia-drm-block.conf` | New | 1 | Block nvidia-drm + nvidia-modeset |
| `.config/modprobe.d/nvidia-blacklist-nouveau.conf` | New | 1 | Blacklist nouveau at boot |
| `.config/modules-load.d/nvidia-compute.conf` | New | 1 | Load nvidia + nvidia-uvm at boot |
| `.config/scripts/prime-run.sh` | New | 3 | PRIME offload wrapper |
| `install.sh` | Modified | 1 | Timeshift prompt + NVIDIA section + mkdir for new dirs |
| `/etc/apt/sources.list.d/nvidia-non-free.list` | New | 1 | non-free repos for nvidia packages |
| `/etc/modprobe.d/z99-nvidia-drm-block.conf` | New | 1 | System-level display module block |
| `/etc/modprobe.d/nvidia-blacklist-nouveau.conf` | New | 1 | System-level nouveau blacklist |
| `/etc/modules-load.d/nvidia-compute.conf` | New | 1 | Boot-time module load |
| `/etc/default/grub.d/99-nouveau-gsp.cfg` | Deleted | 1 | No longer needed |

## Failure Scenarios

| Failure | Phase | Symptom | Recovery |
|---------|-------|---------|----------|
| `nvidia-kernel-dkms` not found in apt | 1 | Package not found | Verify non-free in sources; check `nvidia-non-free.list` |
| DKMS build fails | 1 | Module files missing | Check `dkms build` logs, install `kernel-headers` |
| nvidia-drm loads despite block | 1 | New `/dev/dri/cardN` appears | Check modprobe.d priority; `install /bin/true` may need higher-priority file |
| DKMS cross-kernel infection | 1 | Old kernel breaks after nvidia modules built | Modprobe block is kernel-independent — nvidia-drm never loads on any kernel |
| nvidia-uvm fails to load | 2 | `/dev/nvidia-uvm` missing | Check `dmesg | tail` for nvidia errors; verify nvidia.ko loaded; check udev rule from nvidia-utils |
| nvidia-smi "no devices" | 2 | nvidia.ko loaded but no `/dev/nvidia*` | `dmesg | grep nvidia` for PCI probe failures; verify nouveau not still bound |
| Nouveau conflicts with nvidia | 2 | `modprobe nvidia` fails | `modprobe -r nouveau` first; blacklist takes effect at next boot |
| Sway crashes on reboot | 2 | Black screen | TTY → `timeshift restore` to pre-install snapshot |
| PRIME offload fails | 3 | Falls back to software | Check `DISPLAY` env; verify `nvidia_drm` not loaded; PRIME needs `nvidia.ko` but NOT `nvidia-drm.ko` |
