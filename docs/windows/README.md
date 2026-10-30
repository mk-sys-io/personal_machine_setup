# Windows dual-boot pipeline
> Status: Draft — not yet verified · Phase 2/3 design only

## Vision

Debian is the primary/trusted OS. Windows 11 IoT Enterprise LTSC is a deliberately constrained secondary OS used only to install and play games (FitGirl repacks). There is no shared partition: Linux-side read/write access to the Windows NTFS volume is the transfer mechanism, and games are written directly onto the Windows C: volume as a whole — no `Games` subfolder is created.

## Trust model and current guarantees

Phase 1 leaves internet enabled and does not time-gate or lock down Windows. The Phase 1 installer, launcher, and Ventoy delivery are implemented in code but remain `Draft — not yet verified` until a full run on the target Windows partition `/dev/nvme0n1p4` passes (§8.2). Phase 1/2 controls are friction, not a security boundary; physical access and local admin can bypass. The MAS one-liner is unpinned remote code, and FitGirl repacks are untrusted; neither is isolated by this pipeline.

## Phase map

| Phase | Scope | Status |
|---|---|---|
| Phase 1 | Automated Windows setup installer, launcher, Ventoy delivery, docs rewrite | This plan (Draft — not yet verified) |
| Phase 2 | Linux `ntfs3` access, direct C: volume transfer, Windows network lockdown, kill-switch flag | Future |
| Phase 3 | Daily time-gating via boot manager/firmware, anti-bypass design | Future |

## Current state

| Component | Path | Status |
|---|---|---|
| Phase 1 installer | `windows/windows-setup.ps1` | Draft — not yet verified |
| Launcher | `windows/run-windows-setup.cmd` | Draft — not yet verified |
| Stick delivery | `tools/make-ventoy-stick.sh` | Draft — not yet verified |
| Transfer target | Windows C: volume | Selected by the user; no subfolder is created |
| Linux NTFS access | `docs/windows/file-transfer.md` | Manual, not active |
| Network lockdown | — | Not implemented (Phase 2) |
| Kill switch | — | Not implemented (Phase 2) |
| Time gating | — | Not implemented (Phase 3) |

## End-to-end workflow (current)

1. Games are obtained on Debian.
2. Debian mounts the Windows NTFS partition read/write and writes games directly onto the Windows C: volume; no `Games` subfolder is created.
3. Windows runs offline of any control channel in Phase 1; network disable is Phase 2.

## Setup quickstart

1. Build the Ventoy stick with `tools/make-ventoy-stick.sh` (copies `windows-setup.ps1` and `run-windows-setup.cmd` to the stick root alongside the ISO).
2. Boot Windows on `/dev/nvme0n1p4`, complete OOBE, install all updates.
3. Double-click `run-windows-setup.cmd` on the stick (no arguments). Accept the UAC prompt.
4. A timestamped `windows-setup-YYYYMMDD-HHMMSS.log` is written beside `windows-setup.ps1` on the Ventoy root. The final `Exit code: N` line is logged after the summary.
5. Debian gets qBittorrent from `packages/apt.txt`, not from Windows setup.

## Recovery model

Windows is fresh, games-only, with no user data requiring backup. There is no backup, restore point, rollback, or transactional undo. Rerunning the installer converges from actual state; if Windows is corrupted, reinstall it. Game saves are not protected. The `.log` is diagnostic only and cannot restore the system.

## Manual steps and limitations

- Defender and SmartScreen are intentionally untouched; the user handles FitGirl/MAS exceptions manually.
- NVIDIA DCH display driver is installed by base setup when a `VEN_10DE` GPU is present (via GFE-resolved direct download, Display.Driver only). NVIDIA App is not installed; add it manually if the overlay or driver-update UI is wanted (Windows Update may downgrade a newer driver on LTSC).
- Afterburner + RTSS provide the fullscreen temp panel (top-left/right OSD); RTSS is the only OSD. Never run two overlays together.
- Dark mode is per-user HKCU and may need a new sign-in or Explorer restart to refresh the taskbar and Start; do not kill Explorer.
- Physical access and local admin can bypass Phase 1 controls.

## Document map

- `setup-script.md` — Phase 1 implementation reference.
- `file-transfer.md` — manual Linux→Windows NTFS procedure.
- `kill-switch.md` — Phase 2 future design.
- `time-gating.md` — Phase 3 research.
- `baseline-2026-10-25.md` — historical snapshot.

## Security and legal notes

The MAS one-liner is unpinned remote code; its SHA-256 is logged but the download is not verified. FitGirl repacks are untrusted. Neither is isolated by this pipeline. Phase 1/2 controls are friction, not a security boundary.