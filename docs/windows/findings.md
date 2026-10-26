# Windows state findings

Snapshot captured 2026-10-25 via `windows-state.ps1` (PowerShell, elevated). Log: `windows-state-20261025-072729.log`, JSON: `windows-state-20261025-072729.json`.

## OS

- Windows 11 IoT Enterprise LTSC, 10.0.26100, 64-bit.
- Hostname: DESKTOP-2GMEL99, WORKGROUP domain.
- RAM: 15.69 GB total, 12.44 GB free.
- System drive: C:, system directory C:\WINDOWS\system32.

## Volumes

| Drive | Filesystem | Size (GB) | Free (GB) | Health |
|-------|-----------|-----------|-----------|--------|
| (none) | NTFS | 0.63 | 0.11 | Healthy |
| (none) | FAT32 | 0.95 | 0.90 | Healthy |
| C: | NTFS | 185.48 | 150.59 | Healthy |
| D: | exFAT | 57.76 | 57.02 | Healthy (Ventoy USB) |
| E: | FAT | 0.03 | 0.00 | Healthy (VTOYEFI) |

- C: is the system and boot volume (partition 4, disk 0, offset 295428947968).
- Free space threshold for transfers: ≥ 30 GB required on system drive.

## BitLocker

- All volumes fully decrypted, protection off, no key protectors.
- No encryption barrier for Linux ↔ Windows file access.

## Fast Startup

- Registry `HiberbootEnabled=1` (configured as enabled).
- `hiberfil.sys` absent (0 GB) → Fast Startup is configured but **non-functional** (cannot hibernate without hiberfil.sys).
- `powercfg /a` lists "Fast Startup" as available.
- NTFS is safe to mount from Linux (no hibernation file).
- Registry flag should be cleared for cleanliness (`powercfg /hibernate off`).

## NTFS (C:)

- Cluster size: 4096 bytes (4 KB). Bytes per sector: 512. Bytes per physical sector: 4096.
- Volume is NOT dirty (clean).
- Supports: case-sensitive filenames, Unicode, ACLs, compression, quotas, sparse files, reparse points, encryption (EFS), named streams, transactions, hard links, extended attributes, USN journal.

## Physical disks

| Disk | Type | Size (GB) | Health | OpStatus |
|------|------|-----------|--------|----------|
| BG6 KIOXIA 512GB | SSD | 476.94 | Healthy | OK |
| Silicon-Power 64G (USB) | Unspecified | 57.80 | Healthy | OK |

- No SMART errors. Read/write error totals null (not reported or zero).
- SSD temperature: 52°C. USB temperature: 0°C (not reported).

## Page file

- C:\pagefile.sys, 2944 MB allocated, 0 current usage, 0 peak.

## Open handles

- `handle.exe` (Sysinternals) not installed — open-handle check skipped (minor, not blocking).

## Admin context

- Elevated: yes (user mike, Administrator role).