# Windows setup (dual-boot)

Dual-boot setup: Debian (primary) + Windows 11 IoT Enterprise LTSC on the same machine.
This directory documents the Windows side: state findings, setup automation plan, file transfer, kill switch, and time-gating.

## Context

- Personal machine, no BitLocker, Fast Startup disabled.
- One-way file transfer: Linux → Windows (Linux reads/writes Windows NTFS directly).
- No shared partition — direct NTFS access via ntfs3/ntfs-3g.
- Windows setup is assumed complete (OOBE done, internet connected, all updates installed) before the automation script runs.

## Setup script location

The Windows setup script lives at `windows/windows-setup.ps1` (top-level `windows/` folder, not `tools/`). See [setup-script.md](setup-script.md) for the full plan.

## Files

### Reference (captured state, no action needed)

| File | Purpose |
|------|---------|
| [findings.md](findings.md) | Windows state snapshot (OS, volumes, BitLocker, Fast Startup, disks) |

### Implement now (Phase 2-3)

| File | Purpose |
|------|---------|
| [setup-script.md](setup-script.md) | 14-section Windows setup automation plan |
| [file-transfer.md](file-transfer.md) | Linux → Windows NTFS filesystem access |

### Future enhancement

| File | Purpose |
|------|---------|
| [kill-switch.md](kill-switch.md) | One-way kill switch (Linux writes flag, Windows reads at boot) |
| [time-gating.md](time-gating.md) | Time-gating Windows accessibility during the day |

## Upcoming changes (not yet applied)

### `tools/make-ventoy-stick.sh` — ISO selection

- Add option `a` (all): select and copy ALL ISO files found (from tier1 scan: `~/ISOs/*.iso` + `~/Downloads/*.iso`) to the stick in one go.
- Remove option `s` (scan home): delete the `scan_home_isos()` path and its `s` choice in `pick_iso()`.

This is a separate concern from the Windows setup — it's about the Ventoy stick builder. Track as a future task.