# Windows setup automation script

PowerShell script for Windows 11 IoT Enterprise LTSC. Lives at `windows/windows-setup.ps1` (top-level `windows/` folder, not `tools/`). Assumes Windows setup (OOBE) is complete, internet is connected, all updates are installed, and the script runs as Administrator in elevated PowerShell. PowerShell 5.1+ (built-in) is available.

## Assumptions

- Windows 11 IoT Enterprise LTSC (10.0.26100+).
- Internet connection established and stable.
- All Windows updates installed (pending update check will hard-refuse if Critical/mandatory updates are outstanding).
- Running as Administrator.
- No BitLocker (dropped for now — future enhancement).
- No Office suites (no Ohook activation needed).

## Pending updates check

Uses the Windows Update Agent (WUA) COM API to detect uninstalled updates. Hard-refuses if Critical or mandatory updates are pending.

```powershell
$session = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$result = $searcher.Search("IsInstalled=0")
$pending = $result.Updates

$critical = @($pending | Where-Object { $_.MsrcSeverity -eq "Critical" -or $_.IsMandatory })
$important = @($pending | Where-Object { $_.MsrcSeverity -eq "Important" })

if ($critical.Count -gt 0) {
    Write-Host "CRITICAL/mandatory updates pending:" -ForegroundColor Red
    $critical | ForEach-Object { Write-Host "  - $($_.Title) (KB: $($_.KBArticleIDs -join ','))" }
    throw "Install critical updates before running this script."
}
if ($important.Count -gt 0) {
    Write-Host "WARNING: $($important.Count) important update(s) pending." -ForegroundColor Yellow
}
```

- `IUpdate::MsrcSeverity` returns "Critical", "Important", "Moderate", "Low", or "Unspecified".
- `IUpdate::IsMandatory` is a Boolean indicating mandatory updates.
- `IsMandatory` is more reliable than `MsrcSeverity` for hard-refuse decisions (some driver updates return "Unspecified").

## Internet check

Uses NCSI (Network Connectivity Status Indicator) — the same mechanism Windows uses internally.

```powershell
function Test-InternetConnection {
    param([int]$MaxRetries = 5, [int]$RetryDelaySec = 10)
    for ($i = 0; $i -lt $MaxRetries; $i++) {
        try {
            $nlm = New-Object -ComObject NetworkListManager
            if ($nlm.IsConnectedToInternet) {
                try {
                    $resp = Invoke-WebRequest -Uri "http://www.msftconnecttest.com/connecttest.txt" -UseBasicParsing -TimeoutSec 10
                    if ($resp.StatusCode -eq 200 -and $resp.Content.Trim() -eq "Microsoft Connect Test") {
                        return $true
                    }
                } catch {}
            }
        } catch {}
        if ($i -lt $MaxRetries - 1) { Start-Sleep -Seconds $RetryDelaySec }
    }
    return $false
}
```

- Method 1 (NCSI COM API): instant, no network traffic, uses Windows' own connectivity determination.
- Method 2 (HTTP probe): verifies actual internet access, detects captive portals (redirect/empty response → false).
- Retry loop: 5 attempts, 10 second delay. Abort if no internet after all retries.

## Sections

| # | Section | Internet? | Key actions |
|---|---------|-----------|-------------|
| 1 | Pre-flight | No | Admin check, OS version check (Win 11 IoT Enterprise LTSC), disk space ≥ 30 GB free (dynamic check, not hardcoded) |
| 2 | Pending updates check | Yes | WUA API: hard-refuse if Critical/mandatory; warn if Important |
| 3 | Internet check | Yes | NCSI COM API + HTTP probe, retry 5×/10s. Abort if no internet. |
| 4 | System state capture | No | Reuse diagnostic logic → JSON export (baseline before changes) |
| 5 | Fast Startup disable | No | `powercfg /hibernate off` (removes hiberfil.sys, disables Fast Startup + hibernation) |
| 6 | Debloating | No | `Get-AppxPackage`/`Remove-AppxPackage` for UWP bloat; registry tweaks for telemetry/privacy/Cortana |
| 7 | Security hardening | No | Firewall rules, Defender config, UAC, disable SMBv1, disable RDP (optional) |
| 8 | App installation | Yes | winget: Brave, qBittorrent, Revo Uninstaller |
| 9 | Settings config | No | Registry tweaks (explorer, taskbar, power plan, disable tips/ads) |
| 10 | Activation | Yes | MAS: HWID (Windows only, no Office) |
| 11 | Shared partition | No | Create exFAT partition on NVMe for kill-switch flag + file shuttle (if not exists) |
| 12 | Kill switch setup | No | Flag file + scheduled task at boot |
| 13 | Logging | No | Transcript + JSON export (before/after state) |
| 14 | Summary | No | Print results, failures, manual attention items |

## Debloating (moderate level)

Uses built-in PowerShell cmdlets only (no external dependencies like winutil or Sophia Script). More reliable — no breakage from Windows updates.

```powershell
$bloatApps = @(
    "Microsoft.3DBuilder", "Microsoft.BingWeather", "Microsoft.GetHelp",
    "Microsoft.Getstarted", "Microsoft.Microsoft3DViewer", "Microsoft.MicrosoftOfficeHub",
    "Microsoft.MicrosoftSolitaireCollection", "Microsoft.MixedReality.Portal",
    "Microsoft.Office.OneNote", "Microsoft.People", "Microsoft.SkypeApp",
    "Microsoft.Wallet", "Microsoft.WindowsAlarms", "Microsoft.WindowsCamera",
    "microsoft.windowscommunicationsapps", "Microsoft.WindowsFeedbackHub",
    "Microsoft.WindowsMaps", "Microsoft.WindowsSoundRecorder", "Microsoft.XboxApp",
    "Microsoft.XboxGamingOverlay", "Microsoft.XboxIdentityProvider",
    "Microsoft.XboxSpeechToTextOverlay", "Microsoft.YourPhone", "Microsoft.ZuneMusic",
    "Microsoft.ZuneVideo"
)
foreach ($app in $bloatApps) {
    Get-AppxPackage -Name $app -AllUsers | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
    Get-AppxProvisionedPackage -Online | Where-Object { $_.DisplayName -eq $app } | Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue
}

# Disable telemetry
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection" -Name "AllowTelemetry" -Value 0 -Type DWord -Force
# Disable Cortana
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search" -Name "AllowCortana" -Value 0 -Type DWord -Force
```

## App installation

```powershell
$apps = @(
    "Brave.Brave",              # Brave Browser
    "qBittorrent.qBittorrent",  # qBittorrent
    "RevoUninstaller.RevoUninstaller"  # Revo Uninstaller (free, v2.4.2)
)
foreach ($app in $apps) {
    winget install --id $app --silent --accept-package-agreements --accept-source-agreements
}
```

- winget IDs verified via winget.run.
- Revo Uninstaller: freeware, proprietary license, available from winget community repo (no Microsoft Store required).
- winget on IoT Enterprise LTSC may not have Microsoft Store; all three apps are available from the community repository.

## Activation

MAS (Microsoft Activation Scripts): `irm https://get.activated.win | iex`. HWID method for Windows (permanent digital license). No Office suites installed, so no Ohook needed.

- MAS is not Microsoft-authorized; violates EULA. Legal exposure varies by jurisdiction. Acceptable for personal machine.
- Delivery via `irm|iex` may be blocked by ISP/DNS/TLS. SmartScreen/Defender may flag. Requires admin.

## Fast Startup disable

```powershell
powercfg /hibernate off
```

Removes hiberfil.sys, disabling both hibernation and Fast Startup. Most reliable method. Also frees disk space (hiberfil.sys is typically 40-75% of RAM).

Alternative (keeps hibernation, disables only Fast Startup):
```powershell
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" -Name "HiberbootEnabled" -Value 0
```

## Open questions

1. Debloating level: moderate (confirmed). Aggressive or conservative?
2. Kill switch default: internet allowed or denied on first boot?
3. Time-gating: which hours should Windows be accessible?
4. App list: any additional apps beyond Brave, qBittorrent, Revo Uninstaller?