# Phase 1 — automated Windows setup
> Status: Draft — not yet verified

## Scope and non-goals

Phase 1 automates Windows setup for a games-only secondary OS: installer, launcher, and Ventoy delivery. It leaves internet enabled, does not disable networking, and does not time-gate Windows. Non-goals: no shared partition, no games folder or `C:\Games`, no backup/restore/rollback, no Defender/SmartScreen changes, no game installation, no BitLocker changes.

## Prerequisites

- Windows 11 IoT Enterprise LTSC, build >= 26100, `EditionID = IoTEnterpriseS`.
- 64-bit OS and 64-bit PowerShell process (a 32-bit process is refused before mutation).
- At least 30 GB free on the system drive.
- Internet access and a working `winget`.
- OOBE complete and all updates installed.
- Run elevated as the gaming user (not built-in `Administrator`).

## Invocation and fixed configuration

No CLI parameters. The launcher `run-windows-setup.cmd` takes no arguments and forwards none. Fixed configuration:

- Time zone: `W. Central Africa Standard Time` (no fallback).
- Log directory: `$PSScriptRoot` only; no fallback.
- Log artifacts: `windows-setup-YYYYMMDD-HHMMSS.log` only; no `.transcript.txt` or `.json`.
- Arguments: none; any argument is rejected inside `Main` after logging starts.
- Skips: none; all phases always run.

## Preflight

Read-only checks; any refusal exits `1` before the baseline state capture.

Hard-refuse: not admin, non-`IoTEnterpriseS` OS, 32-bit OS or process, free space < 30 GB, pending Critical/`IsMandatory` updates, no internet (robust multi-layer check), winget unavailable after bootstrap, `W. Central Africa Standard Time` missing. Warn only: built-in `Administrator` profile, BitLocker on, Important updates, WUA COM failure. winget is bootstrapped (App Installer MSIX + deps) when missing; on failure a `MANUAL INSTALL:` label is printed and preflight refuses.

## Execution order

Core system → Debloat → Hardening → Power/Game → Tweaks → Apps → Activation. Every phase always runs; per-item failures are recorded and do not abort later phases.

## Core system changes

- Fast Startup/hibernation off: `powercfg /hibernate off`, `HiberbootEnabled=0`, verify `hiberfil.sys` absent.
- Clock: local RTC (`RealTimeIsUniversal=0` if present and nonzero), set `W. Central Africa Standard Time` only if `tzutil /g` differs, ensure `w32time` is `Auto`/`Running` via `Start-Service`, then `w32tm /resync` (warning-only on failure). No reboot required for clock verification.
- Power plan: `SCHEME_MIN` if not active, else `SCHEME_BALANCED` with warning.
- Sleep/hibernate timeouts: `standby-timeout-ac 0`, `hibernate-timeout-ac 0`; monitor timeout 15 min.
- Game Mode on; Game DVR off.

## Debloat

Removes 25 Appx packages (installed and provisioned), plus telemetry/consumer-features/content-delivery registry rows, disables `DiagTrack` and `dmwappushservice`, and disables 10 scheduled tasks. `Microsoft.WindowsStore` and `Microsoft.DesktopAppInstaller` are retained and guarded. Defender and SmartScreen are not touched.

The 25 Appx packages:

1. Microsoft.3DBuilder
2. Microsoft.BingWeather
3. Microsoft.GetHelp
4. Microsoft.Getstarted
5. Microsoft.Microsoft3DViewer
6. Microsoft.MicrosoftOfficeHub
7. Microsoft.MicrosoftSolitaireCollection
8. Microsoft.MixedReality.Portal
9. Microsoft.Office.OneNote
10. Microsoft.People
11. Microsoft.SkypeApp
12. Microsoft.Wallet
13. Microsoft.WindowsAlarms
14. Microsoft.WindowsCamera
15. microsoft.windowscommunicationsapps
16. Microsoft.WindowsFeedbackHub
17. Microsoft.WindowsMaps
18. Microsoft.WindowsSoundRecorder
19. Microsoft.XboxApp
20. Microsoft.XboxGamingOverlay
21. Microsoft.XboxIdentityProvider
22. Microsoft.XboxSpeechToTextOverlay
23. Microsoft.YourPhone
24. Microsoft.ZuneMusic
25. Microsoft.ZuneVideo

## Tweaks

Per-user HKCU: show file extensions (`HideFileExt=0`), search box mode 1, disable tips/ads (`SoftLandingEnabled=0`, `SubscribedContent-338389Enabled=0`, `SystemPaneSuggestionsEnabled=0`). Dark mode: `AppsUseLightTheme=0` and `SystemUsesLightTheme=0` under `HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize`. There is no system-wide cmdlet; the values apply only to the elevated gaming profile, and a new sign-in or Explorer restart may be needed to refresh the taskbar and Start. Do not kill Explorer.

## Hardening

Firewall profiles enabled with inbound Block/outbound Allow; SMBv1 disabled (feature + server config); RDP denied plus firewall group disabled; LLMNR off; autorun `NoDriveTypeAutoRun=255`; PowerShell v2 disabled; Remote Assistance off; Remote Registry disabled. Any `RestartNeeded` sets the reboot-required flag.

## Applications

- Brave, Revo Uninstaller, Afterburner, and RTSS via winget (official IDs: `Brave.Brave`, `RevoUninstaller.RevoUninstaller`, `Guru3D.Afterburner`, `Guru3D.RTSS`). Already-installed exit `0x8A15010D` (`-1978334963`) is treated as already compliant.
- NVIDIA driver via GPU-gated direct download: detection uses `Win32_VideoController.PNPDeviceID -match 'VEN_10DE'` (works without drivers); resolves the versioned DCH driver URL from NVIDIA's GFE web service using the PCI device ID, logs SHA-256, installs with `-s -n Display.Driver -noreboot` (display driver only — NVIDIA App is NOT installed). Skipped (not failed) when no NVIDIA GPU is present. GeForce Experience is deprecated and is not installed.
- NVIDIA App is intentionally not installed: no reliable silent installer exists that excludes the App/telemetry, and the marketing-page scrape was brittle. Add NVIDIA App manually later if the overlay or driver-update UI is wanted (it installs cleanly on top of the DCH driver). HDMI audio is not installed by the Display.Driver-only path; Windows falls back to the generic High Definition Audio driver (install the full driver package manually if NVIDIA HDAudio is required).
- qBittorrent is declared in `packages/apt.txt` for Debian and is not a Windows app.

Afterburner + RTSS provide the fullscreen temp panel (top-left/right OSD). NVIDIA App is not installed by base setup; add it manually if the NVIDIA Overlay or driver-update UI is wanted (never run two overlays together). Base setup installs the DCH display driver directly; Windows Update may downgrade a newer driver on LTSC (beware).

## Activation

MAS HWID via one-liner download from `https://get.activated.win`, logged with SHA-256, run with `/HWID-NoEditionChange` through the 64-bit PowerShell. Skipped when a non-add-on Windows license reports `LicenseStatus=1`. All `Windows*` license objects are logged; add-on licenses are filtered out. Success requires `LicenseStatus=1`, `LicenseIsAddon=False`, and an unchanged `EditionID`; `GracePeriodRemaining` and `PartialProductKey` are recorded. Activation failure sets exit code `3` and prints the manual command `irm https://get.activated.win | iex` (choose HWID). The download is unpinned remote code; `LicenseStatus=1` is a licensed state, not proof of permanence.

## Logs, state, and exit codes

- One `.log` beside `windows-setup.ps1`: `windows-setup-YYYYMMDD-HHMMSS.log`. No fallback directory; if it cannot be created, abort before mutation. Mid-run write failure warns once and forces exit `2`.
- Compact `STATE BEFORE`/`STATE AFTER` entries from in-memory captures.
- Exit codes: `0` success, `1` preflight/fatal, `2` non-fatal failures or warnings, `3` activation-only. Precedence `1 > 2 > 3 > 0`. Final `Exit code: N` line written after the summary.
- MAS output captured in the `.log`; no `mas.out`/`mas.err`.

## Recovery model

Totally idempotent: every mutation helper reads actual state first and writes only when the target differs. No progress markers, resume files, or "already ran" flags. Rerunning after a crash, interruption, power loss, or manual drift converges. Pending-reboot states such as `DisablePending` are recorded as reboot-required and not reapplied. Non-fatal failures are recorded and retried on the next run. A second immediate run exits `0` with `AlreadyCompliant`/already-installed statuses and changes no configuration. No backup, restore point, rollback, or transactional undo exists; if Windows is corrupted, reinstall it. The `.log` is diagnostic only; game saves are not protected.

## Manual follow-ups

- Install or update NVIDIA drivers via NVIDIA App (Windows Update is baseline only).
- Handle FitGirl/MAS exceptions in Defender manually.
- Correct the Linux RTC if Windows shows the wrong time: `sudo hwclock --systohc --localtime` (Linux RTC is currently 8 hours behind local time).
- Reboot if the reboot-required flag is set.

## Verification record

Draft — not yet verified. Completion requires a full run on `/dev/nvme0n1p4` passing §8.2; docs flip to `Implemented` with `Last verified` only then.

## Future phases

- Phase 2: Linux `ntfs3` access, direct C: volume transfer, network lockdown, kill-switch flag.
- Phase 3: daily time-gating via boot manager/firmware.

See `kill-switch.md` and `time-gating.md`. No Phase 2/3 implementation steps are in this document.