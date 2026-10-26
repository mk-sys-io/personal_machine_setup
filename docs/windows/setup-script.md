# Phase 1 — automated Windows setup
> Status: Draft — not yet verified

PowerShell 5.1 script for Windows 11 IoT Enterprise LTSC, delivered on the Ventoy stick as `windows/windows-setup.ps1` with `windows/run-windows-setup.cmd`. This document describes the Phase 1 design; the script is not yet implemented.

## Scope and non-goals

Phase 1 prepares a fresh games-oriented Windows install: core system settings, debloat, hardening, tweaks, Brave, Revo Uninstaller, and MAS activation.

- No backup, restore point, rollback, or transactional undo.
- No shared partition and no Games folder are created; games install directly on the Windows C: volume.
- Defender and SmartScreen are intentionally untouched.
- No network lockdown or time-gating is implemented; see [kill-switch.md](kill-switch.md) and [time-gating.md](time-gating.md).
- qBittorrent is not a Windows app. It is declared in `packages/apt.txt` for Debian.

## Prerequisites

- Windows 11 IoT Enterprise LTSC, build `26100` or later, `EditionID = IoTEnterpriseS`.
- 64-bit Windows and 64-bit PowerShell.
- Internet access and `winget.exe`.
- Administrator privileges.
- At least 30 GB free on the system drive.
- `Tunisia Standard Time` available in `tzutil /l`.
- BitLocker off is recommended; protection on produces a warning, not a refusal.

## Invocation and fixed configuration

Run `windows\run-windows-setup.cmd` as Administrator. The launcher starts `windows-setup.ps1` with `-NoProfile -ExecutionPolicy Bypass -File` and forwards no arguments. Direct invocation is also supported:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows-setup.ps1
```

| Setting | Value |
|---|---|
| Time zone | `Tunisia Standard Time` |
| Log directory | `$PSScriptRoot` only; no fallback |
| Log artifacts | `windows-setup-YYYYMMDD-HHMMSS.log`, `.transcript.txt`, `.json` |
| Arguments | None; any argument is rejected after logging starts |
| Skips | None; every phase runs on every invocation |

Exit codes: `0` success, `1` preflight/fatal failure, `2` completed with failures or warnings, `3` activation-only failure. Precedence is `1` > `2` > `3` > `0`.

## Preflight

Preflight is read-only. A refusal exits `1` before state capture and before any mutation.

| Check | Result |
|---|---|
| Administrator | Refuse |
| Built-in `Administrator` profile | Warn; HKCU and winget would target the admin profile |
| OS build and edition | Refuse if build < 26100 or edition is not `IoTEnterpriseS` |
| 64-bit OS and process | Refuse; 32-bit PowerShell resolves `System32` to SysWOW64 |
| System-drive free space below 30 GB | Refuse |
| BitLocker protection on | Warn only |
| Pending Windows updates | Refuse on Critical or `IsMandatory`; warn on Important |
| Internet | Refuse; apps and activation always run |
| `winget.exe` | Refuse; apps always run |
| Time-zone ID missing | Refuse; never substitute another timezone |

## Execution order

1. Initialize logging and reject arguments.
2. Run preflight.
3. Capture the before-state.
4. Core system: Fast Startup/hibernation and clock.
5. Debloat.
6. Hardening.
7. Power plan, timeouts, Game Mode, and Game DVR.
8. Tweaks, including dark mode.
9. Install Brave and Revo Uninstaller.
10. Activation.
11. Capture the after-state, write JSON, print summary, and exit.

Every phase runs even if an earlier phase records a failure. Only preflight refusal exits early.

## Core system changes

- Disable hibernation and Fast Startup with `powercfg.exe /hibernate off`; verify `hiberfil.sys` is absent.
- Read `RealTimeIsUniversal`; set it to `0` only if present and nonzero.
- Set `Tunisia Standard Time` only when `tzutil /g` differs.
- Enable `w32time`, configure `time.windows.com`, and run `w32tm /resync`.
- If time sync fails, warn and continue.
- Verify timezone, local time, and `w32tm /query /status`.

The current Linux RTC is 8 hours behind local time. Windows reads a wrong clock until sync succeeds. If it remains wrong after reboot, correct Linux with `sudo hwclock --systohc --localtime` or `sudo timedatectl set-local-rtc 1 --adjust-system-clock`.

## Debloat

Removes the following Appx packages and their provisioned packages. Missing packages are already compliant; per-item failures are recorded and do not stop later items or phases.

```text
Microsoft.3DBuilder
Microsoft.BingWeather
Microsoft.GetHelp
Microsoft.Getstarted
Microsoft.Microsoft3DViewer
Microsoft.MicrosoftOfficeHub
Microsoft.MicrosoftSolitaireCollection
Microsoft.MixedReality.Portal
Microsoft.Office.OneNote
Microsoft.People
Microsoft.SkypeApp
Microsoft.Wallet
Microsoft.WindowsAlarms
Microsoft.WindowsCamera
microsoft.windowscommunicationsapps
Microsoft.WindowsFeedbackHub
Microsoft.WindowsMaps
Microsoft.WindowsSoundRecorder
Microsoft.XboxApp
Microsoft.XboxGamingOverlay
Microsoft.XboxIdentityProvider
Microsoft.XboxSpeechToTextOverlay
Microsoft.YourPhone
Microsoft.ZuneMusic
Microsoft.ZuneVideo
```

`Microsoft.WindowsStore` and `Microsoft.DesktopAppInstaller` must survive. If either is missing after debloat, the run records a warning.

Telemetry, Cortana, consumer features, content delivery, search suggestions, Copilot, Widgets, and Chat are disabled through registry values. `DiagTrack` and `dmwappushservice` are disabled, and the listed Application Experience, Customer Experience Improvement Program, Feedback, Autocheck, Disk Diagnostic, and Windows Error Reporting tasks are disabled when present.

## Tweaks

- Show file extensions: `HideFileExt=0`.
- Use the wide search box: `SearchboxTaskbarMode=1`.
- Disable tips and ads through `ContentDeliveryManager`.
- Enable Game Mode and disable Game DVR.
- Set the high-performance power plan, with balanced as a warned fallback.
- Disable sleep and hibernation timeouts; set the monitor timeout to 15 minutes.

### Dark mode

Dark mode is per-user and is stored in `HKCU`, so the script must run in the gaming user's profile, not a different administrator account.

```powershell
$p = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
if (-not (Test-Path -LiteralPath $p)) { New-Item -Path $p -Force | Out-Null }
New-ItemProperty -Path $p -Name AppsUseLightTheme -Value 0 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $p -Name SystemUsesLightTheme -Value 0 -PropertyType DWord -Force | Out-Null
```

- `0` is dark; `1` is light.
- `AppsUseLightTheme` controls apps; `SystemUsesLightTheme` controls shell surfaces.
- The key may not exist on a clean install, so create it before writing values.
- Sign out or restart Explorer for the shell to refresh. The script does not kill Explorer.
- There is no documented system-wide PowerShell cmdlet for dark mode. Do not apply this to other user profiles.
- Reference: <https://learn.microsoft.com/en-us/windows/apps/develop/settings/settings-common>

## Hardening

- Enable Windows Firewall on all profiles: inbound Block, outbound Allow.
- Disable SMBv1 through both the optional feature and `Set-SmbServerConfiguration`.
- Deny RDP and disable the Remote Desktop firewall group.
- Disable LLMNR.
- Set autorun to `255`.
- Disable PowerShell v2.
- Disable Remote Assistance and Remote Registry.
- Leave Defender and SmartScreen unchanged.

Optional features or localized firewall groups that are unavailable are recorded as warnings, not fatal errors.

## Applications

```powershell
$apps = @(
    'Brave.Brave',
    'RevoUninstaller.RevoUninstaller'
)
```

- Install silently with winget and accept package/source agreements.
- Treat already-installed apps as compliant.
- Treat `0x8A15010D` (`APPINSTALLER_CLI_ERROR_INSTALL_ALREADY_INSTALLED`) as already installed if the presence check misses it.
- qBittorrent is installed only on Debian from `packages/apt.txt`.

## Activation

The script skips MAS when Windows is already licensed:

```powershell
$licenses = @(Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL" |
    Where-Object { $_.Name -like 'Windows*' -and -not $_.LicenseIsAddon })
$isLicensed = @($licenses | Where-Object { $_.LicenseStatus -eq 1 }).Count -gt 0
```

`LicenseStatus = 1` means currently licensed. It is not proof of permanent activation. Values `2`, `3`, `4`, and `6` are grace states; `5` is Notification. `PartialProductKey` only means a key is present. The script logs `Name`, `LicenseStatus`, `GracePeriodRemaining`, and `PartialProductKey` for every matching product.

`slmgr /xpr` is an optional manual permanence check; its output is localized and is not parsed. `Get-OSLicenseInfo` is not assumed to exist; its documentation lists it only for Windows Server vNext Build 29651 and Windows 11 with KB5124008. Neither is required for the skip decision.

If not licensed, the script downloads `https://get.activated.win`, logs its SHA-256, and runs MAS with `/HWID-NoEditionChange` in a separate Windows PowerShell process. Run the script from 64-bit PowerShell so the child process is 64-bit. The flag preserves the installed edition. After the run, it re-reads the license and requires `LicenseStatus = 1` plus an unchanged `EditionID`.

- MAS is third-party and not Microsoft-authorized; it violates the EULA. Legal exposure varies by jurisdiction.
- The one-liner is unpinned remote code. Logging the SHA-256 does not authenticate or verify it.
- SmartScreen or Defender may block it; the script does not change those protections.
- If activation fails, the script records the manual command, sets the activation-only failure flag, continues remaining work, and exits `3` unless another failure raises the code to `2`.

Manual fallback: `irm https://get.activated.win | iex`, then choose HWID.

## Logs, state, and exit codes

Each run writes three same-base files beside `windows-setup.ps1` on the Ventoy root:

- `windows-setup-YYYYMMDD-HHMMSS.log` — timestamped line log.
- `windows-setup-YYYYMMDD-HHMMSS.transcript.txt` — transcript of console and native output.
- `windows-setup-YYYYMMDD-HHMMSS.json` — diagnostic before/after state, per-step results, reboot flag, manual follow-ups, and exit code.

Rules:

- `.log` lines use `yyyy-MM-dd HH:mm:ss [LEVEL] message` with `INFO`, `WARN`, `ERROR`, or `FATAL`.
- On collision, append `-2`, `-3`, and so on. Never overwrite, rotate, or delete prior runs.
- There is no `C:\Windows\Logs\WindowsSetup` fallback and no `-LogDir` option.
- `.log` creation failure aborts before mutation and exits `1`.
- Transcript-start failure warns and exits `2` after otherwise successful work; a mid-run log write failure warns once and exits `2`.
- MAS output goes to the `.log` and transcript. No `mas.out` or `mas.err` files are created.
- The exit code is computed before the JSON is written; the JSON and `.log` contain the same value.
- The final `Exit code: N` line is logged before the transcript stops; a transcript-stop failure cannot replace it.
- The JSON is diagnostic only; it is not a backup and cannot restore the system.

## Recovery model

- The setup is idempotent. Helpers read actual state and skip already-compliant values.
- Rerun after a crash, interruption, power loss, or manual drift. There are no progress markers or resume files.
- Per-item failures are retried on the next run.
- No backup, restore point, rollback, or transactional undo exists. If Windows is corrupted, reinstall it.
- Game saves are not protected by this pipeline.

## Manual follow-ups

- Sign out or restart Explorer after dark mode is applied.
- Check Windows activation manually with `slmgr /xpr` if permanence matters.
- If time is still wrong after reboot, correct the Linux RTC and reboot Windows.
- If MAS is blocked by the network or Defender, run the manual command and recheck the license.
- Install games directly on the Windows C: volume.

## Verification record

| Item | Status |
|---|---|
| Static checks on Linux | Not yet run against an implementation |
| Target run on `/dev/nvme0n1p4` | Not yet run |
| Second-run idempotency | Not yet run |
| Logging artifacts and exit codes | Not yet verified |
| Documentation | Draft — not yet verified |

Do not claim `Implemented` or add `Last verified` until the full target run passes on `/dev/nvme0n1p4`.

## Future phases

Network lockdown, the boot-time kill switch, and time-gating are not implemented. See [kill-switch.md](kill-switch.md) and [time-gating.md](time-gating.md).
