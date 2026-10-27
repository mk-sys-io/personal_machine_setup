# Windows dual-boot pipeline
> Status: Draft — not yet verified · Phase 2/3 design only

Dual-boot machine: Debian (primary) plus Windows 11 IoT Enterprise LTSC. This directory documents the Windows side: Phase 1 setup automation, manual file transfer, and the Phase 2/3 kill-switch and time-gating designs.

## Vision

Keep Debian as the trusted daily system and use Windows only for games and Windows-only software. Phase 1 makes a fresh Windows install usable with a repeatable script; later phases add network control and boot-time gating.

## Trust model and current guarantees

- Windows is not isolated from Debian at the filesystem level. Linux can read and write the Windows NTFS partition.
- Linux → Windows file transfer is a workflow policy, not a filesystem restriction.
- Phase 1 leaves internet enabled.
- Defender and SmartScreen are intentionally untouched.
- No backup, restore point, rollback, or transactional undo exists. If Windows is corrupted, reinstall it.
- The log is diagnostic only. Game saves are not protected.
- MAS is third-party, EULA-violating, and unpinned remote code. FitGirl repacks are untrusted. Neither is sandboxed by this pipeline.

## Phase map

| Phase | Scope | Status |
|---|---|---|
| Phase 1 | Automated Windows setup: core system, debloat, hardening, tweaks, Brave, Revo, activation | Draft — not yet verified |
| Phase 2 | Network lockdown and boot-time kill switch | Not implemented |
| Phase 3 | Time-gating Windows accessibility | Not implemented |

## Current state

| Component | Path | Status |
|---|---|---|
| Phase 1 installer | `windows/windows-setup.ps1` | Not yet created |
| Launcher | `windows/run-windows-setup.cmd` | Not yet created |
| Ventoy delivery | `tools/make-ventoy-stick.sh` | Copy step not yet added |
| Transfer target | Windows C: volume | Selected by the user; no subfolder is created |
| Linux NTFS access | `docs/windows/file-transfer.md` | Manual, not active |
| Network lockdown | — | Not implemented (Phase 2) |
| Kill switch | — | Not implemented (Phase 2) |
| Time gating | — | Not implemented (Phase 3) |

The completion gate is a full run on the Windows partition `/dev/nvme0n1p4`. Until it passes, every Phase 1 document uses `Draft — not yet verified`; no `Implemented` or `Last verified` claim is valid.

## End-to-end workflow (target)

1. Boot Debian and build the Ventoy stick.
2. Copy the planned `windows-setup.ps1` and `run-windows-setup.cmd` to the stick root once they exist.
3. Boot Windows and run the launcher as Administrator.
4. Review the `.log` beside the script.
5. Reboot if required, then rerun to confirm idempotency.
6. Boot Debian and use the Windows C: volume directly for game files if needed.

## Setup quickstart

Once the Phase 1 files exist:

1. Confirm Windows setup (OOBE) is complete, internet works, and updates are installed.
2. Run `windows\run-windows-setup.cmd` from the Ventoy root as Administrator.
3. Let every phase run. Do not pass arguments; there are no skip switches.
4. Read `windows-setup-YYYYMMDD-HHMMSS.log`.
5. Sign out after dark mode is applied so the shell refreshes.
6. Reboot if the summary says a reboot is required, then run the same launcher again.

Each run creates a new `.log` beside `windows-setup.ps1` on the Ventoy root. qBittorrent is installed on Debian from `packages/apt.txt`, not by Windows.

See [setup-script.md](setup-script.md) for the full Phase 1 reference.

## Recovery model

- The installer is idempotent: rerun it and it converges from actual system state.
- There are no progress markers or resume files.
- Interrupted runs are recovered by running the launcher again.
- There is no backup or restore path. Reinstall Windows if the install is corrupted.
- The log cannot restore settings. Game saves are not protected.

## Manual steps and limitations

- Activation is not guaranteed to succeed through the network; the log contains the manual MAS command.
- Dark mode is per-user. If elevation uses different administrator credentials, the values land in the wrong profile.
- BitLocker protection is only warned about; the script does not unlock or disable it.
- Pending updates can refuse the run before any changes.
- The Windows partition must be cleanly unmounted before Linux writes to it.

## Document map

| File | Purpose |
|---|---|
| [setup-script.md](setup-script.md) | Phase 1 installer reference: preflight, phases, activation, dark mode, logging, exit codes, recovery |
| [file-transfer.md](file-transfer.md) | Manual Linux → Windows NTFS access; not currently mounted |
| [kill-switch.md](kill-switch.md) | Future Phase 2 design; not implemented |
| [time-gating.md](time-gating.md) | Future Phase 3 research; not implemented |
| [findings.md](findings.md) | Historical state snapshot from 2026-10-25; not a current prerequisite |

## Security and legal notes

- The MAS one-liner is unpinned remote code. The SHA-256 log records what ran; it does not verify the publisher.
- MAS is not Microsoft-authorized and violates the EULA. Legal exposure varies by jurisdiction.
- Defender and SmartScreen may block MAS or game repacks; this pipeline does not bypass or reconfigure them.
- Windows malware can write files that Debian later reads. Do not treat the NTFS mount as an isolation boundary.
- Physical access can bypass boot-level controls. Phase 2/3 designs do not claim to prevent that.

## Upcoming changes (not yet applied)

### `tools/make-ventoy-stick.sh` — ISO selection

- Add option `a` (all): select and copy all discovered ISOs to the stick.
- Remove option `s` (scan home) and its scan path.

This is separate from the Windows setup and remains a future stick-builder change.
