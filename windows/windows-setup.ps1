#Requires -Version 5.1
# PSScriptAnalyzer: Disable RuleName=PSAvoidUsingWriteHost
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Fixed runtime configuration (no CLI parameters)
$script:TimeZoneId = 'W. Central Africa Standard Time'
$script:InvocationArgs = @($args)
$script:LogDir = $null
$script:RunBase = $null
$script:LogPath = $null
$script:LogWriteFailed = $false
$script:HadFailure = $false
$script:HadWarning = $false
$script:ActivationFailed = $false
$script:RebootRequired = $false
$script:StepResults = New-Object System.Collections.Generic.List[object]
$script:BeforeState = $null
$script:AfterState = $null

function New-StepResult {
    param([string]$Name)
    [pscustomobject]@{
        Name    = $Name
        Status  = 'Pending'   # Applied|AlreadyCompliant|Failed|Warning
        Detail  = ''
        Started = Get-Date
        Ended   = $null
    }
}

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Log "=== $Message ==="
}

function Write-Result {
    param($Result)
    $detail = ''
    if ($null -ne $Result.Detail -and $Result.Detail -ne '') { $detail = " - $($Result.Detail)" }
    Write-Log ("{0}: {1}{2}" -f $Result.Name, $Result.Status, $detail)
}

function Write-Log {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Message,
        [ValidateSet('INFO','WARN','ERROR','FATAL')][string]$Level = 'INFO'
    )
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    if ($null -eq $script:LogPath) { return }
    try {
        Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 -ErrorAction Stop
    } catch {
        if (-not $script:LogWriteFailed) {
            $script:LogWriteFailed = $true
            Write-Host ('{0} [WARN] log write failed: {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $_.Exception.Message)
        }
    }
}

function Initialize-Logging {
    $root = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($root)) { $root = Split-Path -Parent $PSCommandPath }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "Script directory not found: $root"
    }

    $script:LogDir = $root
    $base = 'windows-setup-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
    $suffix = 0
    do {
        $candidate = if ($suffix -eq 0) { $base } else { '{0}-{1}' -f $base, $suffix }
        $script:LogPath = Join-Path $root ($candidate + '.log')
        $suffix++
    } while (Test-Path -LiteralPath $script:LogPath)

    New-Item -ItemType File -Path $script:LogPath -Force -ErrorAction Stop | Out-Null
    $script:RunBase = $candidate

    Write-Log "Script: $PSCommandPath"
    Write-Log "PowerShell: $($PSVersionTable.PSVersion)"
    Write-Log "User: $env:USERDOMAIN\$env:USERNAME"
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        Write-Log "OS: $($os.Caption) $($os.BuildNumber)"
    } catch {
        Write-Log "OS: (unavailable) $($_.Exception.Message)" WARN
    }
    Write-Log "Log: $script:LogPath"
}

function Invoke-Mutation {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Action
    )
    $r = New-StepResult -Name $Name
    try {
        $status = & $Action
        if ($status -in @('Applied','AlreadyCompliant','Failed','Warning')) {
            $r.Status = $status
        } elseif ($r.Status -eq 'Pending') {
            $r.Status = 'Applied'
        }
        if ($r.Status -eq 'Failed') {
            if ($Name -eq 'Activation') { $script:ActivationFailed = $true }
            else { $script:HadFailure = $true }
        }
    } catch {
        $r.Status = 'Failed'
        $r.Detail = $_.Exception.Message
        $script:HadFailure = $true
    } finally {
        $r.Ended = Get-Date
        $script:StepResults.Add($r)
        Write-Result $r
    }
}

function Invoke-External {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$AllowNonZero
    )
    Write-Log "RUN: $FilePath $($Arguments -join ' ')"
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = @(& $FilePath @Arguments 2>&1)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldEap
    }
    $output | ForEach-Object { Write-Log ([string]$_) }
    Write-Log "EXIT: $FilePath -> $code"
    if (-not $AllowNonZero -and $code -ne 0) {
        throw "$FilePath failed with exit code $code"
    }
}

function Set-RegValue {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]$Value,
        [ValidateSet('DWord','String','QWord','ExpandString','MultiString','Binary')]
        [string]$Type = 'DWord'
    )
    $current = Get-RegValue -Path $Path -Name $Name
    if ($null -ne $current -and $current -eq $Value) { return 'AlreadyCompliant' }
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
    New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
    return 'Applied'
}

function Get-RegValue {
    param([string]$Path, [string]$Name)
    try {
        (Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop).$Name
    } catch {
        $null
    }
}

# Read-only probe for a power setting's current AC value (seconds).
# Uses powercfg aliases (universal Windows constants, not hardware-specific).
function Get-PowerSettingAc {
    param([string]$SubAlias, [string]$SettingAlias)
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & powercfg /query SCHEME_CURRENT $SubAlias $SettingAlias 2>&1
        $text = ($out | Out-String)
        if ($text -match 'Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)') {
            return [Convert]::ToInt32($matches[1], 16)
        }
    } catch { } finally { $ErrorActionPreference = $oldEap }
    return $null
}

# --- mutation helpers (WP4/WP5) ---
function Remove-AppxSafe {
    param([Parameter(Mandatory)][string]$PackageFullName)
    $present = @()
    try { $present = @(Get-AppxPackage -AllUsers -ErrorAction Stop | Where-Object { $_.PackageFullName -eq $PackageFullName }) } catch { $present = @() }
    if ($present.Count -eq 0) { return 'AlreadyCompliant' }
    Remove-AppxPackage -Package $PackageFullName -AllUsers -ErrorAction Stop
    return 'Applied'
}

function Remove-ProvisionedAppxSafe {
    param([Parameter(Mandatory)][string]$PackageName)
    $present = @()
    try { $present = @(Get-AppxProvisionedPackage -Online -ErrorAction Stop | Where-Object { $_.PackageName -eq $PackageName }) } catch { $present = @() }
    if ($present.Count -eq 0) { return 'AlreadyCompliant' }
    Remove-AppxProvisionedPackage -Online -PackageName $PackageName -ErrorAction Stop | Out-Null
    return 'Applied'
}

function Disable-FeatureSafe {
    param([Parameter(Mandatory)][string]$FeatureName)
    $feat = $null
    try { $feat = Get-WindowsOptionalFeature -Online -FeatureName $FeatureName -ErrorAction Stop } catch {
        Write-Log "Optional feature unavailable: $FeatureName ($($_.Exception.Message))" WARN
        return 'AlreadyCompliant'
    }
    if ($null -eq $feat) { Write-Log "Optional feature not found: $FeatureName" WARN; return 'AlreadyCompliant' }
    if ($feat.State -in @('Disabled','DisablePending')) { return 'AlreadyCompliant' }
    $res = Disable-WindowsOptionalFeature -Online -FeatureName $FeatureName -NoRestart -ErrorAction Stop
    if ($null -ne $res -and $res.RestartNeeded) { $script:RebootRequired = $true }
    return 'Applied'
}

function Disable-TaskSafe {
    param([Parameter(Mandatory)][string]$TaskPath)
    $name = Split-Path $TaskPath -Leaf
    $dir = Split-Path $TaskPath -Parent
    if ([string]::IsNullOrWhiteSpace($dir)) { $dir = '\' }
    if (-not $dir.EndsWith('\')) { $dir += '\' }
    $task = $null
    try { $task = Get-ScheduledTask -TaskPath $dir -TaskName $name -ErrorAction Stop } catch {
        Write-Log "Scheduled task not found: $TaskPath ($($_.Exception.Message))" WARN
        return 'AlreadyCompliant'
    }
    if ($null -eq $task) { Write-Log "Scheduled task not found: $TaskPath" WARN; return 'AlreadyCompliant' }
    if ($task.State -eq 'Disabled') { return 'AlreadyCompliant' }
    Disable-ScheduledTask -TaskPath $dir -TaskName $name -ErrorAction Stop | Out-Null
    return 'Applied'
}

function Set-ServiceSafe {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$StartupType,
        [switch]$StopIfRunning
    )
    $svc = $null
    try { $svc = Get-Service -Name $Name -ErrorAction Stop } catch {
        Write-Log "Service not found: $Name ($($_.Exception.Message))" WARN
        return 'AlreadyCompliant'
    }
    if ($null -eq $svc) { Write-Log "Service not found: $Name" WARN; return 'AlreadyCompliant' }
    $changed = $false
    if (-not [string]::IsNullOrWhiteSpace($StartupType)) {
        if ($svc.StartType.ToString() -ine $StartupType) {
            Set-Service -Name $Name -StartupType $StartupType -ErrorAction Stop
            $changed = $true
        }
    }
    if ($StopIfRunning -and $svc.Status -eq 'Running' -and $StartupType -ieq 'Disabled') {
        Stop-Service -Name $Name -Force -ErrorAction Stop
        $changed = $true
    }
    if ($changed) { return 'Applied' }
    return 'AlreadyCompliant'
}

function Set-FirewallSafe {
    $profiles = $null
    try { $profiles = Get-NetFirewallProfile -ErrorAction Stop } catch {
        Write-Log "NetFirewallProfile cmdlet unavailable: $($_.Exception.Message)" WARN
        return 'AlreadyCompliant'
    }
    if ($null -eq $profiles) { Write-Log 'NetFirewallProfile returned nothing.' WARN; return 'AlreadyCompliant' }
    $allCompliant = $true
    foreach ($p in $profiles) {
        if (-not $p.Enabled -or $p.DefaultInboundAction -ne 'Block' -or $p.DefaultOutboundAction -ne 'Allow') { $allCompliant = $false; break }
    }
    if ($allCompliant) { return 'AlreadyCompliant' }
    Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True -DefaultInboundAction Block -DefaultOutboundAction Allow -ErrorAction Stop
    return 'Applied'
}

function Set-SmbServerSafe {
    $cfg = $null
    try { $cfg = Get-SmbServerConfiguration -ErrorAction Stop } catch {
        Write-Log "SmbServerConfiguration cmdlet unavailable: $($_.Exception.Message)" WARN
        return 'AlreadyCompliant'
    }
    if ($null -eq $cfg) { Write-Log 'Get-SmbServerConfiguration returned nothing.' WARN; return 'AlreadyCompliant' }
    if (-not $cfg.EnableSMB1Protocol) { return 'AlreadyCompliant' }
    Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force -ErrorAction Stop
    return 'Applied'
}

function Disable-FirewallGroupSafe {
    param([string]$Group)
    if ([string]::IsNullOrWhiteSpace($Group)) { return 'AlreadyCompliant' }
    $rules = @()
    try { $rules = @(Get-NetFirewallRule -DisplayGroup $Group -ErrorAction Stop) } catch {
        Write-Log "Firewall group not found or cmdlet unavailable: $Group ($($_.Exception.Message))" WARN
        return 'AlreadyCompliant'
    }
    if ($rules.Count -eq 0) { Write-Log "Firewall group not found: $Group" WARN; return 'AlreadyCompliant' }
    $allDisabled = $true
    foreach ($r in $rules) { if ($r.Enabled) { $allDisabled = $false; break } }
    if ($allDisabled) { return 'AlreadyCompliant' }
    Disable-NetFirewallRule -DisplayGroup $Group -ErrorAction Stop | Out-Null
    return 'Applied'
}

# --- preflight ---
function Test-Admin {
    $p = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    return [bool]$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-SupportedOS {
    try {
        $nt = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
        if ([int]$nt.CurrentBuildNumber -lt 26100) { return $false }
        if ($nt.EditionID -ne 'IoTEnterpriseS') { return $false }
        return $true
    } catch { return $false }
}

function Test-FreeSpace {
    try {
        $drive = $env:SystemDrive.TrimEnd(':')
        $psd = Get-PSDrive -Name $drive -ErrorAction Stop
        return ($psd.Free -ge 30GB)
    } catch { return $false }
}

function Test-BitLocker {
    try {
        $v = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop
        return $v.ProtectionStatus
    } catch { return $null }
}

function Test-Internet {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # Supporting DNS probe (system resolver, DoH-aware) - informational only.
    $dnsOk = $false
    try {
        $r = Resolve-DnsName -Name 'dns.msftconnecttest.com' -Type A -ErrorAction Stop
        $dnsOk = ($r.IPAddress -contains '131.107.255.255')
    } catch { $dnsOk = $false }
    if ($dnsOk) { Write-Log 'Internet: NCSI DNS OK.' INFO } else { Write-Log 'Internet: NCSI DNS not resolved (informational).' WARN }

    # Optional OS hint (NLM) - may throw on LTSC IoT if netprofm is disabled.
    $nlmOk = $false
    try { $nlm = New-Object -ComObject NetworkListManager; $nlmOk = [bool]$nlm.IsConnectedToInternet } catch { $nlmOk = $false }
    Write-Log "Internet: NLM=$nlmOk (informational only)." INFO

    $expected = 'Microsoft Connect Test'
    $proxy = [System.Net.WebRequest]::GetSystemWebProxy()
    $connected = $false
    $captive = $false

    foreach ($url in @('http://www.msftconnecttest.com/connecttest.txt','http://ipv6.msftconnecttest.com/connecttest.txt')) {
        try {
            $req = [System.Net.HttpWebRequest]::Create($url)
            $req.Timeout = 10000
            $req.ReadWriteTimeout = 10000
            $req.Proxy = $proxy
            $req.UseDefaultCredentials = $true
            $req.AllowAutoRedirect = $false
            $req.KeepAlive = $false
            $resp = $req.GetResponse()
            try {
                if ($resp.StatusCode -ne [System.Net.HttpStatusCode]::OK) { continue }
                $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
                $body = $sr.ReadToEnd(); $sr.Dispose()
                if ($body -match [regex]::Escape($expected)) {
                    $connected = $true
                    break
                } else {
                    $captive = $true
                }
            } finally { $resp.Dispose() }
        } catch [System.Net.WebException] {
            $we = $_.Exception
            if ($we.Response) {
                $code = [int]$we.Response.StatusCode
                if ($code -ge 300 -and $code -lt 400) { $captive = $true }
            }
        } catch { }
    }

    if ($connected) {
        Write-Log "Internet: connected (http body validated; dns=$dnsOk nlm=$nlmOk)." INFO
    } else {
        Write-Log "Internet: NOT connected (dns=$dnsOk nlm=$nlmOk)." WARN
        if ($captive) { Write-Log 'Internet: captive portal or proxy auth suspected (redirect or unexpected body).' WARN }
    }
    return $connected
}

function Test-Winget {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { return $false }
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & winget --version 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
    finally { $ErrorActionPreference = $oldEap }
}

function Install-Winget {
    try {
        Write-Log 'Bootstrapping winget (App Installer) for LTSC IoT Enterprise...' INFO
        # Enable sideloading
        $unlock = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock'
        if (-not (Test-Path $unlock)) { New-Item -Path $unlock -Force | Out-Null }
        Set-ItemProperty -Path $unlock -Name AllowAllTrustedApps -Value 1 -Type DWord -Force

        $archToken = $env:PROCESSOR_ARCHITECTURE
        $archDir = switch ($archToken) {
            'AMD64' { 'x64' }
            'x86'   { 'x86' }
            'ARM64' { 'arm64' }
            default { $null }
        }
        if (-not $archDir) {
            Write-Log "Install-Winget: unsupported architecture '$archToken'." WARN
            return $false
        }

        $work = Join-Path $env:TEMP 'winget-bootstrap'
        if (Test-Path $work) { Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $work -Force | Out-Null

        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'

        # Resolve latest release tag (pinned fallback on API failure)
        $tag = 'v1.29.380'
        try {
            $api = Invoke-RestMethod -Uri 'https://api.github.com/repos/microsoft/winget-cli/releases/latest' -UseBasicParsing -TimeoutSec 30
            if ($api.tag_name) { $tag = $api.tag_name }
        } catch {
            Write-Log "Install-Winget: GitHub API failed ($($_.Exception.Message)); using pinned $tag." WARN
        }
        Write-Log "Install-Winget: using release $tag" INFO

        $base = "https://github.com/microsoft/winget-cli/releases/download/$tag"
        $bundle = 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle'
        $depsZip = 'DesktopAppInstaller_Dependencies.zip'

        $bundlePath = Join-Path $work $bundle
        $depsPath = Join-Path $work $depsZip
        try {
            Invoke-WebRequest -Uri "$base/$bundle" -UseBasicParsing -OutFile $bundlePath -TimeoutSec 300
            Invoke-WebRequest -Uri "$base/$depsZip" -UseBasicParsing -OutFile $depsPath -TimeoutSec 300
        } catch {
            Write-Log "Install-Winget: download failed: $($_.Exception.Message)" ERROR
            Write-Log 'MANUAL INSTALL: winget is not available and bootstrap failed. Install App Installer manually from https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/deployment/install-winget-windows-iot (download the MSIX bundle + dependency zip from https://github.com/microsoft/winget-cli/releases, enable sideloading, install deps then bundle), then rerun this script.' ERROR
            return $false
        }

        $depsDir = Join-Path $work 'deps'
        try { Expand-Archive -Path $depsPath -DestinationPath $depsDir -Force } catch {
            Write-Log "Install-Winget: extract failed: $($_.Exception.Message)" ERROR
            Write-Log 'MANUAL INSTALL: winget is not available and bootstrap failed. Install App Installer manually from https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/deployment/install-winget-windows-iot (download the MSIX bundle + dependency zip from https://github.com/microsoft/winget-cli/releases, enable sideloading, install deps then bundle), then rerun this script.' ERROR
            return $false
        }

        $archDepDir = Join-Path $depsDir $archDir
        if (-not (Test-Path $archDepDir)) {
            Write-Log "Install-Winget: no dependency folder for $archDir." ERROR
            Write-Log 'MANUAL INSTALL: winget is not available and bootstrap failed. Install App Installer manually from https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/deployment/install-winget-windows-iot (download the MSIX bundle + dependency zip from https://github.com/microsoft/winget-cli/releases, enable sideloading, install deps then bundle), then rerun this script.' ERROR
            return $false
        }

        $oldEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $depFailed = $false
        try {
            $depFiles = @(Get-ChildItem $archDepDir -Filter *.appx | Sort-Object Name) + @(Get-ChildItem $archDepDir -Filter *.msix | Sort-Object Name)
            foreach ($dep in $depFiles) {
                & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -Command "Add-AppxPackage -Path '$($dep.FullName)'" 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    $depFailed = $true
                    Write-Log "Install-Winget: dependency failed ($($dep.Name), exit $LASTEXITCODE)." ERROR
                    break
                }
            }
        } finally { $ErrorActionPreference = $oldEap }
        if ($depFailed) {
            Write-Log 'MANUAL INSTALL: winget is not available and bootstrap failed. Install App Installer manually from https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/deployment/install-winget-windows-iot (download the MSIX bundle + dependency zip from https://github.com/microsoft/winget-cli/releases, enable sideloading, install deps then bundle), then rerun this script.' ERROR
            return $false
        }

        try {
            Add-AppxPackage -Path $bundlePath -ErrorAction Stop
        } catch {
            Write-Log "Install-Winget: bundle install failed: $($_.Exception.Message)" ERROR
            Write-Log 'MANUAL INSTALL: winget is not available and bootstrap failed. Install App Installer manually from https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/deployment/install-winget-windows-iot (download the MSIX bundle + dependency zip from https://github.com/microsoft/winget-cli/releases, enable sideloading, install deps then bundle), then rerun this script.' ERROR
            return $false
        }

        $wa = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'
        if ($env:Path -notlike "*$wa*") { $env:Path = "$env:Path;$wa" }

        # Re-check
        $oldEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & winget --version 2>&1 | Out-Null
            $ok = ($LASTEXITCODE -eq 0)
        } finally { $ErrorActionPreference = $oldEap }
        if (-not $ok) {
            Write-Log 'Install-Winget: winget still not available after bootstrap.' ERROR
            Write-Log 'MANUAL INSTALL: winget is not available and bootstrap failed. Install App Installer manually from https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/deployment/install-winget-windows-iot (download the MSIX bundle + dependency zip from https://github.com/microsoft/winget-cli/releases, enable sideloading, install deps then bundle), then rerun this script.' ERROR
            return $false
        }
        Write-Log 'Install-Winget: winget bootstrapped successfully.' INFO
        return $true
    } catch {
        Write-Log "Install-Winget: unexpected error: $($_.Exception.Message)" ERROR
        Write-Log 'MANUAL INSTALL: winget is not available and bootstrap failed. Install App Installer manually from https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/deployment/install-winget-windows-iot (download the MSIX bundle + dependency zip from https://github.com/microsoft/winget-cli/releases, enable sideloading, install deps then bundle), then rerun this script.' ERROR
        return $false
    }
}

function Test-Timezone {
    try {
        $list = & tzutil /l 2>&1
        return (($list -join "`n") -match [regex]::Escape($script:TimeZoneId))
    } catch { return $false }
}

function Get-PendingUpdates {
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("IsInstalled=0")
    $updates = New-Object System.Collections.Generic.List[object]
    foreach ($u in $result.Updates) {
        $updates.Add([pscustomobject]@{
            Title       = $u.Title
            Severity    = $u.MsrcSeverity
            IsMandatory = [bool]$u.IsMandatory
        }) | Out-Null
    }
    return $updates
}

function Invoke-Preflight {
    $refused = $false

    if (-not (Test-Admin)) {
        Write-Log 'Preflight: not running as Administrator.' ERROR
        $refused = $true
    }

    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        if ($id.User.Value -match '-500$') {
            Write-Log 'Preflight: elevated account is built-in Administrator; HKCU/winget target that profile.' WARN
            $script:HadWarning = $true
        }
    } catch { }

    if (-not (Test-SupportedOS)) {
        Write-Log 'Preflight: unsupported OS (requires Windows 11 IoT Enterprise LTSC, build >= 26100).' ERROR
        $refused = $true
    }

    if (-not [Environment]::Is64BitOperatingSystem) {
        Write-Log 'Preflight: 32-bit operating system not supported.' ERROR
        $refused = $true
    }
    if (-not [Environment]::Is64BitProcess) {
        Write-Log 'Preflight: 32-bit PowerShell process not supported.' ERROR
        $refused = $true
    }

    if (-not (Test-FreeSpace)) {
        Write-Log 'Preflight: less than 30 GB free on system drive.' ERROR
        $refused = $true
    }

    $bl = Test-BitLocker
    if ($bl -eq 'On' -or $bl -eq 1) {
        Write-Log 'Preflight: BitLocker protection is ON.' WARN
        $script:HadWarning = $true
    }

    # Pending updates via COM (WUA), with a 60s timeout; failure is a warning, not a refusal.
    $wuFailed = $false
    $updates = $null
    $wuJob = Start-Job -ScriptBlock {
        $session = New-Object -ComObject Microsoft.Update.Session
        $searcher = $session.CreateUpdateSearcher()
        $result = $searcher.Search("IsInstalled=0")
        $out = New-Object System.Collections.Generic.List[object]
        foreach ($u in $result.Updates) {
            $out.Add([pscustomobject]@{ Title = $u.Title; Severity = $u.MsrcSeverity; IsMandatory = [bool]$u.IsMandatory })
        }
        return $out
    }
    $done = Wait-Job -Job $wuJob -Timeout 60
    if ($null -eq $done) {
        Stop-Job -Job $wuJob -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $wuJob -Force -ErrorAction SilentlyContinue
        Write-Log 'Preflight: pending updates check timed out (60s).' WARN
        $script:HadWarning = $true
        $wuFailed = $true
    } else {
        $updates = Receive-Job -Job $wuJob -ErrorAction SilentlyContinue
        $state = $wuJob.State
        Remove-Job -Job $wuJob -Force -ErrorAction SilentlyContinue
        if ($state -ne 'Completed' -or $null -eq $updates) {
            Write-Log "Preflight: pending updates check failed (state=$state)." WARN
            $script:HadWarning = $true
            $wuFailed = $true
        }
    }
    if (-not $wuFailed) {
        $critical = @($updates | Where-Object { $_.Severity -eq 'Critical' -or $_.IsMandatory })
        if ($critical.Count -gt 0) {
            Write-Log "Preflight: $($critical.Count) critical/mandatory pending update(s)." ERROR
            $refused = $true
        }
        $important = @($updates | Where-Object { $_.Severity -eq 'Important' })
        if ($important.Count -gt 0) {
            Write-Log "Preflight: $($important.Count) important pending update(s)." WARN
            $script:HadWarning = $true
        }
    }

    if (-not (Test-Internet)) {
        Write-Log 'Preflight: no internet connectivity.' ERROR
        $refused = $true
    }

    if (-not (Test-Winget)) {
        Write-Log 'Preflight: winget not available; attempting bootstrap.' WARN
        $script:HadWarning = $true
        $bootstrapped = Install-Winget
        if (-not $bootstrapped -or -not (Test-Winget)) {
            Write-Log 'Preflight: winget not available.' ERROR
            $refused = $true
        }
    }

    if (-not (Test-Timezone)) {
        Write-Log 'Preflight: W. Central Africa Standard Time not available.' ERROR
        $refused = $true
    }

    return [pscustomobject]@{ Refused = $refused }
}

# --- state capture (WP2) ---
function Get-StateSnapshot {
    try {
        # OS / volumes (guarded)
        $os = $null
        try { $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop | Select-Object Caption, Version, BuildNumber, OSArchitecture } catch { Write-Log "State: OS capture failed: $($_.Exception.Message)" WARN }
        $vols = @()
        try { $vols = @(Get-CimInstance Win32_LogicalDisk -ErrorAction Stop | Select-Object DeviceID, FileSystem, Size, FreeSpace) } catch { Write-Log "State: Volumes capture failed: $($_.Exception.Message)" WARN }

        # BitLocker (optional)
        $bl = $null
        try { $bl = @(Get-BitLockerVolume -ErrorAction Stop | Select-Object MountPoint, ProtectionStatus, VolumeStatus) } catch { Write-Log "State: BitLocker capture failed: $($_.Exception.Message)" WARN }

        # Firewall (optional)
        $fw = $null
        try { $fw = @(Get-NetFirewallProfile -ErrorAction Stop | Select-Object Name, Enabled, DefaultInboundAction, DefaultOutboundAction) } catch { Write-Log "State: Firewall capture failed: $($_.Exception.Message)" WARN }

        # Features
        $feats = @()
        try {
            $feats = @(Get-WindowsOptionalFeature -Online -ErrorAction Stop |
                Where-Object { $_.FeatureName -in @('SMB1Protocol', 'MicrosoftWindowsPowerShellV2', 'MicrosoftWindowsPowerShellV2Root') } |
                Select-Object FeatureName, State)
        } catch { Write-Log "State: Features capture failed: $($_.Exception.Message)" WARN }

        # Activation (all Windows* license objects with a partial key)
        $lics = @()
        try {
            $lics = @(Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL" -ErrorAction Stop |
                Where-Object { $_.Name -like 'Windows*' } |
                Select-Object Name, EditionID, LicenseStatus, LicenseIsAddon, GracePeriodRemaining, PartialProductKey)
        } catch { Write-Log "State: Activation capture failed: $($_.Exception.Message)" WARN }

        # Pending updates
        $pu = @()
        try { $pu = @(Get-PendingUpdates) } catch { Write-Log "State: PendingUpdates capture failed: $($_.Exception.Message)" WARN }

        # Appx / provisioned
        $appx = @()
        try { $appx = @(Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue | Select-Object Name, PackageFullName) } catch { Write-Log "State: Appx capture failed: $($_.Exception.Message)" WARN }
        $prov = @()
        try { $prov = @(Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue | Select-Object DisplayName) } catch { Write-Log "State: Provisioned capture failed: $($_.Exception.Message)" WARN }

        # Registry target values (section 5.9-5.12); null if absent.
        $regTargets = @(
            @{ Path = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'; Name = 'HiberbootEnabled' },
            @{ Path = 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation'; Name = 'RealTimeIsUniversal' },
            @{ Path = 'HKCU:\Software\Microsoft\GameBar'; Name = 'AutoGameModeEnabled' },
            @{ Path = 'HKCU:\System\GameConfigStore'; Name = 'GameDVR_Enabled' },
            @{ Path = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR'; Name = 'AppCaptureEnabled' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection'; Name = 'AllowTelemetry' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection'; Name = 'DoNotShowFeedbackNotifications' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search'; Name = 'AllowCortana' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name = 'DisableWindowsConsumerFeatures' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name = 'DisableSoftLanding' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name = 'DisableCloudOptimizedContent' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name = 'DisableWindowsSpotlightFeatures' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'ContentDeliveryAllowed' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'OemPreInstalledAppsEnabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'PreInstalledAppsEnabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SilentInstalledAppsEnabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SoftLandingEnabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SystemPaneSuggestionsEnabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SubscribedContent-338388Enabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SubscribedContent-338389Enabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SubscribedContent-353694Enabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SubscribedContent-353696Enabled' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'; Name = 'SubscribedContent-353697Enabled' },
            @{ Path = 'HKCU:\SOFTWARE\Policies\Microsoft\Windows\Explorer'; Name = 'DisableSearchBoxSuggestions' },
            @{ Path = 'HKCU:\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot'; Name = 'TurnOffWindowsCopilot' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot'; Name = 'TurnOffWindowsCopilot' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'; Name = 'TaskbarDa' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'; Name = 'TaskbarMn' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'; Name = 'ShowSyncProviderNotifications' },
            @{ Path = 'HKLM:\System\CurrentControlSet\Control\Terminal Server'; Name = 'fDenyTSConnections' },
            @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient'; Name = 'EnableMulticast' },
            @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer'; Name = 'NoDriveTypeAutoRun' },
            @{ Path = 'HKLM:\SYSTEM\CurrentControlSet\Control\Remote Assistance'; Name = 'fAllowToGetHelp' },
            @{ Path = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'; Name = 'AppsUseLightTheme' },
            @{ Path = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'; Name = 'SystemUsesLightTheme' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'; Name = 'HideFileExt' },
            @{ Path = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Search'; Name = 'SearchboxTaskbarMode' }
        )
        $reg = @{}
        foreach ($t in $regTargets) {
            if (-not $reg.ContainsKey($t.Path)) { $reg[$t.Path] = @{} }
            $reg[$t.Path][$t.Name] = Get-RegValue -Path $t.Path -Name $t.Name
        }

        # Winget presence
        $winget = @{}
        foreach ($app in @('Brave.Brave', 'RevoUninstaller.RevoUninstaller', 'Guru3D.Afterburner', 'Guru3D.RTSS')) {
            $oldEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                & winget list --id $app --exact 2>&1 | Out-Null
                $winget[$app] = ($LASTEXITCODE -eq 0)
            } catch { $winget[$app] = $false }
            finally { $ErrorActionPreference = $oldEap }
        }
        $winget['NVIDIA driver'] = (Test-Path "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe")

        $snap = [pscustomobject]@{
            Timestamp      = (Get-Date).ToString('o')
            Computer       = $env:COMPUTERNAME
            User           = "$env:USERDOMAIN\$env:USERNAME"
            OS             = $os
            Volumes        = $vols
            BitLocker      = $bl
            FastStartup    = @{
                HiberbootEnabled = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' 'HiberbootEnabled'
                HiberfilPresent  = (Test-Path "$env:SystemDrive\hiberfil.sys")
            }
            Clock          = @{
                RealTimeIsUniversal = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation' 'RealTimeIsUniversal'
                TimeZone            = ((& tzutil /g 2>&1) | Out-String).Trim()
                LocalTime           = (Get-Date).ToString('o')
            }
            Appx           = $appx
            Provisioned    = $prov
            Firewall       = $fw
            Features       = $feats
            Registry       = $reg
            Winget         = $winget
            Activation     = $lics
            DarkMode       = @{
                AppsUseLightTheme    = Get-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' 'AppsUseLightTheme'
                SystemUsesLightTheme = Get-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' 'SystemUsesLightTheme'
            }
            PendingUpdates = $pu
        }
        return $snap
    } catch {
        Write-Log "State capture failed: $($_.Exception.Message)" WARN
        $script:HadWarning = $true
        return $null
    }
}

function Write-StateSummary {
    param($Before, $After, $Results)
    Write-Log '--- STATE BEFORE ---'
    if ($null -eq $Before) {
        Write-Log '  (capture failed)'
    } else {
        Write-Log "  Timestamp: $($Before.Timestamp)"
        Write-Log "  Computer/User: $($Before.Computer) / $($Before.User)"
        if ($null -ne $Before.OS) { Write-Log "  OS: $($Before.OS.Caption) $($Before.OS.BuildNumber) ($($Before.OS.OSArchitecture))" }
        foreach ($v in @($Before.Volumes)) { Write-Log "  Volume $($v.DeviceID): $($v.FileSystem) free=$($v.FreeSpace) size=$($v.Size)" }
        Write-Log "  FastStartup: HiberbootEnabled=$($Before.FastStartup.HiberbootEnabled) hiberfil=$($Before.FastStartup.HiberfilPresent)"
        Write-Log "  Clock: tz=$($Before.Clock.TimeZone) RTIU=$($Before.Clock.RealTimeIsUniversal)"
        foreach ($p in @($Before.Firewall)) { Write-Log "  Firewall $($p.Name): enabled=$($p.Enabled) in=$($p.DefaultInboundAction) out=$($p.DefaultOutboundAction)" }
        foreach ($f in @($Before.Features)) { Write-Log "  Feature $($f.FeatureName): $($f.State)" }
        foreach ($l in @($Before.Activation)) { Write-Log "  License: $($l.Name) edition=$($l.EditionID) status=$($l.LicenseStatus) addon=$($l.LicenseIsAddon) grace=$($l.GracePeriodRemaining) key=$($l.PartialProductKey)" }
        Write-Log "  DarkMode: AppsUseLightTheme=$($Before.DarkMode.AppsUseLightTheme) SystemUsesLightTheme=$($Before.DarkMode.SystemUsesLightTheme)"
        Write-Log "  PendingUpdates: $(@($Before.PendingUpdates).Count)"
        Write-Log "  Winget: $($Before.Winget | ConvertTo-Json -Compress)"
    }

    Write-Log '--- STATE AFTER ---'
    if ($null -eq $After) {
        Write-Log '  (capture failed)'
    } else {
        Write-Log "  Timestamp: $($After.Timestamp)"
        Write-Log "  Computer/User: $($After.Computer) / $($After.User)"
        if ($null -ne $After.OS) { Write-Log "  OS: $($After.OS.Caption) $($After.OS.BuildNumber) ($($After.OS.OSArchitecture))" }
        foreach ($v in @($After.Volumes)) { Write-Log "  Volume $($v.DeviceID): $($v.FileSystem) free=$($v.FreeSpace) size=$($v.Size)" }
        Write-Log "  FastStartup: HiberbootEnabled=$($After.FastStartup.HiberbootEnabled) hiberfil=$($After.FastStartup.HiberfilPresent)"
        Write-Log "  Clock: tz=$($After.Clock.TimeZone) RTIU=$($After.Clock.RealTimeIsUniversal)"
        foreach ($p in @($After.Firewall)) { Write-Log "  Firewall $($p.Name): enabled=$($p.Enabled) in=$($p.DefaultInboundAction) out=$($p.DefaultOutboundAction)" }
        foreach ($f in @($After.Features)) { Write-Log "  Feature $($f.FeatureName): $($f.State)" }
        foreach ($l in @($After.Activation)) { Write-Log "  License: $($l.Name) edition=$($l.EditionID) status=$($l.LicenseStatus) addon=$($l.LicenseIsAddon) grace=$($l.GracePeriodRemaining) key=$($l.PartialProductKey)" }
        Write-Log "  DarkMode: AppsUseLightTheme=$($After.DarkMode.AppsUseLightTheme) SystemUsesLightTheme=$($After.DarkMode.SystemUsesLightTheme)"
        Write-Log "  PendingUpdates: $(@($After.PendingUpdates).Count)"
        Write-Log "  Winget: $($After.Winget | ConvertTo-Json -Compress)"
    }
}

# --- phase functions (stubs; WP3-WP6) ---
function Invoke-CoreSystem {
    $changed = $false
    $hardFailed = $false

    # 1. Fast Startup / hibernation off
    $hiberboot = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' 'HiberbootEnabled'
    $hiberfilPresent = (Test-Path "$env:SystemDrive\hiberfil.sys")
    if ($hiberfilPresent -or ($null -ne $hiberboot -and $hiberboot -ne 0)) {
        $oldEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $out = & powercfg /hibernate off 2>&1
            $code = $LASTEXITCODE
            $out | ForEach-Object { Write-Log ([string]$_) }
            Write-Log "EXIT: powercfg /hibernate off -> $code"
        } catch {
            Write-Log "powercfg /hibernate off threw: $($_.Exception.Message)" WARN
        } finally {
            $ErrorActionPreference = $oldEap
        }
        Set-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' 'HiberbootEnabled' 0 | Out-Null
        $changed = $true
    }

    # 2. Clock: RealTimeIsUniversal (only if present and nonzero)
    $rtiu = Get-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation' 'RealTimeIsUniversal'
    if ($null -ne $rtiu -and $rtiu -ne 0) {
        Set-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation' 'RealTimeIsUniversal' 0 | Out-Null
        $changed = $true
    }

    # 3. Timezone
    $currentTz = ''
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $currentTz = ((& tzutil /g 2>&1) | Out-String).Trim() } catch { $currentTz = '' }
    finally { $ErrorActionPreference = $oldEap }

    if ($currentTz -ne $script:TimeZoneId) {
        try {
            Invoke-External tzutil @('/s', $script:TimeZoneId)
            $verifyTz = ''
            $ErrorActionPreference = 'Continue'
            try { $verifyTz = ((& tzutil /g 2>&1) | Out-String).Trim() } catch { $verifyTz = '' }
            finally { $ErrorActionPreference = $oldEap }
            if ($verifyTz -ne $script:TimeZoneId) { throw "tzutil /s did not apply (got '$verifyTz')" }
            $changed = $true
        } catch {
            Write-Log "Timezone set failed: $($_.Exception.Message)" ERROR
            $hardFailed = $true
            $script:HadFailure = $true
        }
    }

    # 4. w32time service
    $svc = $null
    try { $svc = Get-CimInstance Win32_Service -Filter "Name='w32time'" -ErrorAction Stop } catch { $svc = $null }
    if ($null -eq $svc) {
        Write-Log 'w32time service not found.' ERROR
        $hardFailed = $true
        $script:HadFailure = $true
    } else {
        if ($svc.StartMode -ne 'Auto') {
            try { Set-Service -Name w32time -StartupType Automatic -ErrorAction Stop; $changed = $true }
            catch { Write-Log "Set-Service w32time failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
        }
        if ($svc.State -ne 'Running') {
            try { Start-Service -Name w32time -ErrorAction Stop; $changed = $true }
            catch { Write-Log "Start-Service w32time failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
        }
    }

    # 5. w32tm config + resync (always run; failure is a warning)
    try { Invoke-External w32tm @('/config', '/manualpeerlist:time.windows.com', '/syncfromflags:manual', '/update') }
    catch { Write-Log "w32tm /config failed: $($_.Exception.Message)" WARN; $script:HadWarning = $true }
    try { Invoke-External w32tm @('/resync') }
    catch { Write-Log "w32tm /resync failed: $($_.Exception.Message)" WARN; $script:HadWarning = $true }

    # 6. Verify
    $verifyTz2 = ''
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $verifyTz2 = ((& tzutil /g 2>&1) | Out-String).Trim() } catch { $verifyTz2 = '' }
    finally { $ErrorActionPreference = $oldEap }
    if ($verifyTz2 -ne $script:TimeZoneId) {
        Write-Log "Timezone verification failed (got '$verifyTz2')." ERROR
        $hardFailed = $true
        $script:HadFailure = $true
    }
    try { Invoke-External w32tm @('/query', '/status') -AllowNonZero } catch { }

    Write-Log "Core system: changed=$changed hardFailed=$hardFailed"

    if ($hardFailed) { return 'Failed' }
    if ($changed) { return 'Applied' }
    return 'AlreadyCompliant'
}

function Invoke-PowerAndGame {
    $changed = $false
    $hardFailed = $false

    # Power plan: High Performance if not already active
    $activeGuid = ''
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & powercfg /getactivescheme 2>&1
        $text = ($out | Out-String)
        if ($text -match 'Power Scheme GUID:\s*([0-9a-fA-F-]+)') { $activeGuid = $matches[1].ToLower() }
    } catch { } finally { $ErrorActionPreference = $oldEap }

    $highPerfGuid = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'
    if ($activeGuid -ne $highPerfGuid) {
        try {
            Invoke-External powercfg @('/setactive', 'SCHEME_MIN')
            $changed = $true
        } catch {
            Write-Log "powercfg SCHEME_MIN failed; falling back to balanced: $($_.Exception.Message)" WARN
            $script:HadWarning = $true
            try { Invoke-External powercfg @('/setactive', 'SCHEME_BALANCED'); $changed = $true }
            catch { Write-Log "powercfg SCHEME_BALANCED failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
        }
    }

    # Sleep/hibernate/monitor timeouts (AC)
    $curStandby = Get-PowerSettingAc 'SUB_SLEEP' 'STANDBYIDLE'
    if ($null -eq $curStandby -or $curStandby -ne 0) {
        try { Invoke-External powercfg @('/change', 'standby-timeout-ac', '0'); $changed = $true }
        catch { Write-Log "standby-timeout-ac failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    }
    $curHibernate = Get-PowerSettingAc 'SUB_SLEEP' 'HIBERNATEIDLE'
    if ($null -eq $curHibernate -or $curHibernate -ne 0) {
        try { Invoke-External powercfg @('/change', 'hibernate-timeout-ac', '0'); $changed = $true }
        catch { Write-Log "hibernate-timeout-ac failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    }
    $curVideo = Get-PowerSettingAc 'SUB_VIDEO' 'VIDEOIDLE'
    if ($null -eq $curVideo -or $curVideo -ne 900) {
        try { Invoke-External powercfg @('/change', 'monitor-timeout-ac', '15'); $changed = $true }
        catch { Write-Log "monitor-timeout-ac failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    }

    # Game Mode (HKCU)
    $gm = Get-RegValue 'HKCU:\Software\Microsoft\GameBar' 'AutoGameModeEnabled'
    if ($null -eq $gm -or $gm -ne 1) {
        Set-RegValue 'HKCU:\Software\Microsoft\GameBar' 'AutoGameModeEnabled' 1 | Out-Null
        $changed = $true
    }

    # Game DVR (HKCU)
    $gd = Get-RegValue 'HKCU:\System\GameConfigStore' 'GameDVR_Enabled'
    if ($null -eq $gd -or $gd -ne 0) {
        Set-RegValue 'HKCU:\System\GameConfigStore' 'GameDVR_Enabled' 0 | Out-Null
        $changed = $true
    }
    $ac = Get-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR' 'AppCaptureEnabled'
    if ($null -eq $ac -or $ac -ne 0) {
        Set-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR' 'AppCaptureEnabled' 0 | Out-Null
        $changed = $true
    }

    if ($hardFailed) { return 'Failed' }
    if ($changed) { return 'Applied' }
    return 'AlreadyCompliant'
}
function Invoke-Debloat {
    try {
        $BloatApps = @(
            'Microsoft.3DBuilder','Microsoft.BingWeather','Microsoft.GetHelp','Microsoft.Getstarted',
            'Microsoft.Microsoft3DViewer','Microsoft.MicrosoftOfficeHub','Microsoft.MicrosoftSolitaireCollection',
            'Microsoft.MixedReality.Portal','Microsoft.Office.OneNote','Microsoft.People','Microsoft.SkypeApp',
            'Microsoft.Wallet','Microsoft.WindowsAlarms','Microsoft.WindowsCamera','microsoft.windowscommunicationsapps',
            'Microsoft.WindowsFeedbackHub','Microsoft.WindowsMaps','Microsoft.WindowsSoundRecorder','Microsoft.XboxApp',
            'Microsoft.XboxGamingOverlay','Microsoft.XboxIdentityProvider','Microsoft.XboxSpeechToTextOverlay',
            'Microsoft.YourPhone','Microsoft.ZuneMusic','Microsoft.ZuneVideo'
        )

        foreach ($name in $BloatApps) {
            $r = New-StepResult -Name "Appx: $name"
            $failed = $false
            $removed = $false
            foreach ($pkg in @(Get-AppxPackage -Name $name -AllUsers -ErrorAction SilentlyContinue)) {
                try { Remove-AppxSafe $pkg.PackageFullName | Out-Null; $removed = $true }
                catch { $failed = $true; $r.Detail = $_.Exception.Message }
            }
            $prov = @(Get-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -eq $name })
            foreach ($p in $prov) {
                try { Remove-ProvisionedAppxSafe $p.PackageName | Out-Null; $removed = $true }
                catch { $failed = $true; $r.Detail = $_.Exception.Message }
            }
            if ($failed) { $r.Status = 'Failed'; $script:HadFailure = $true }
            elseif ($removed) { $r.Status = 'Applied' }
            else { $r.Status = 'AlreadyCompliant' }
            $r.Ended = Get-Date
            $script:StepResults.Add($r)
            Write-Result $r
        }

        $regChanged = $false
        $regFailed = $false
        $regRows = @(
            @{ Path='HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection'; Name='AllowTelemetry'; Value=0 },
            @{ Path='HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection'; Name='DoNotShowFeedbackNotifications'; Value=1 },
            @{ Path='HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search'; Name='AllowCortana'; Value=0 },
            @{ Path='HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name='DisableWindowsConsumerFeatures'; Value=1 },
            @{ Path='HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name='DisableSoftLanding'; Value=1 },
            @{ Path='HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name='DisableCloudOptimizedContent'; Value=1 },
            @{ Path='HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'; Name='DisableWindowsSpotlightFeatures'; Value=1 }
        )
        $cdm = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'
        foreach ($n in @('ContentDeliveryAllowed','OemPreInstalledAppsEnabled','PreInstalledAppsEnabled','SilentInstalledAppsEnabled','SoftLandingEnabled','SystemPaneSuggestionsEnabled','SubscribedContent-338388Enabled','SubscribedContent-338389Enabled','SubscribedContent-353694Enabled','SubscribedContent-353696Enabled','SubscribedContent-353697Enabled')) {
            $regRows += @{ Path=$cdm; Name=$n; Value=0 }
        }
        $regRows += @{ Path='HKCU:\SOFTWARE\Policies\Microsoft\Windows\Explorer'; Name='DisableSearchBoxSuggestions'; Value=1 }
        foreach ($p in @('HKCU:\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot','HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot')) {
            $regRows += @{ Path=$p; Name='TurnOffWindowsCopilot'; Value=1 }
        }
        foreach ($n in @('TaskbarDa','TaskbarMn','ShowSyncProviderNotifications')) {
            $regRows += @{ Path='HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'; Name=$n; Value=0 }
        }

        $rr = New-StepResult -Name 'Debloat registry'
        foreach ($row in $regRows) {
            try {
                $st = Set-RegValue -Path $row.Path -Name $row.Name -Value $row.Value
                if ($st -eq 'Applied') { $regChanged = $true }
            } catch {
                $regFailed = $true
                $rr.Detail = $_.Exception.Message
                $script:HadFailure = $true
            }
        }
        if ($regFailed) { $rr.Status = 'Failed' }
        elseif ($regChanged) { $rr.Status = 'Applied' }
        else { $rr.Status = 'AlreadyCompliant' }
        $rr.Ended = Get-Date
        $script:StepResults.Add($rr)
        Write-Result $rr

        foreach ($svcName in @('DiagTrack','dmwappushservice')) {
            $rs = New-StepResult -Name "Service: $svcName"
            try {
                $st = Set-ServiceSafe -Name $svcName -StartupType Disabled -StopIfRunning
                $rs.Status = $st
                if ($st -eq 'Failed') { $script:HadFailure = $true }
            } catch {
                $rs.Status = 'Failed'
                $rs.Detail = $_.Exception.Message
                $script:HadFailure = $true
            }
            $rs.Ended = Get-Date
            $script:StepResults.Add($rs)
            Write-Result $rs
        }

        $tasks = @(
            '\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser',
            '\Microsoft\Windows\Application Experience\ProgramDataUpdater',
            '\Microsoft\Windows\Application Experience\StartupAppTask',
            '\Microsoft\Windows\Customer Experience Improvement Program\Consolidator',
            '\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip',
            '\Microsoft\Windows\Feedback\Siuf\DmClient',
            '\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload',
            '\Microsoft\Windows\Autochk\Proxy',
            '\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector',
            '\Microsoft\Windows\Windows Error Reporting\QueueReporting'
        )
        foreach ($t in $tasks) {
            $rt = New-StepResult -Name "Task: $t"
            try {
                $st = Disable-TaskSafe -TaskPath $t
                $rt.Status = $st
                if ($st -eq 'Failed') { $script:HadFailure = $true }
            } catch {
                $rt.Status = 'Failed'
                $rt.Detail = $_.Exception.Message
                $script:HadFailure = $true
            }
            $rt.Ended = Get-Date
            $script:StepResults.Add($rt)
            Write-Result $rt
        }

        foreach ($keep in @('Microsoft.WindowsStore','Microsoft.DesktopAppInstaller')) {
            if (-not (Get-AppxPackage -Name $keep -AllUsers -ErrorAction SilentlyContinue)) {
                Write-Log "INFO: $keep not present (expected on IoT LTSC; non-fatal)." INFO
            }
        }
    } catch {
        Write-Log "FATAL in Invoke-Debloat: $($_.Exception.Message)" ERROR
        $script:HadFailure = $true
    }
}

function Invoke-Hardening {
    $changed = $false
    $hardFailed = $false

    try { $st = Set-FirewallSafe; if ($st -eq 'Applied') { $changed = $true } elseif ($st -eq 'Failed') { $hardFailed = $true; $script:HadFailure = $true } }
    catch { Write-Log "Firewall hardening failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    try { $st = Disable-FeatureSafe 'SMB1Protocol'; if ($st -eq 'Applied') { $changed = $true } elseif ($st -eq 'Failed') { $hardFailed = $true; $script:HadFailure = $true } }
    catch { Write-Log "SMB1Protocol disable failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    try { $st = Set-SmbServerSafe; if ($st -eq 'Applied') { $changed = $true } elseif ($st -eq 'Failed') { $hardFailed = $true; $script:HadFailure = $true } }
    catch { Write-Log "SMB server config failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    try { $st = Set-RegValue 'HKLM:\System\CurrentControlSet\Control\Terminal Server' 'fDenyTSConnections' 1; if ($st -eq 'Applied') { $changed = $true } }
    catch { Write-Log "RDP deny reg failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    try { $st = Disable-FirewallGroupSafe 'Remote Desktop'; if ($st -eq 'Applied') { $changed = $true } elseif ($st -eq 'Failed') { $hardFailed = $true; $script:HadFailure = $true } }
    catch { Write-Log "Remote Desktop firewall group failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    try { $st = Set-RegValue 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient' 'EnableMulticast' 0; if ($st -eq 'Applied') { $changed = $true } }
    catch { Write-Log "LLMNR reg failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    try { $st = Set-RegValue 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer' 'NoDriveTypeAutoRun' 255; if ($st -eq 'Applied') { $changed = $true } }
    catch { Write-Log "Autorun reg failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    foreach ($fn in @('MicrosoftWindowsPowerShellV2Root','MicrosoftWindowsPowerShellV2')) {
        try { $st = Disable-FeatureSafe $fn; if ($st -eq 'Applied') { $changed = $true } elseif ($st -eq 'Failed') { $hardFailed = $true; $script:HadFailure = $true } }
        catch { Write-Log "PSv2 disable failed ($fn): $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    }

    try { $st = Set-RegValue 'HKLM:\SYSTEM\CurrentControlSet\Control\Remote Assistance' 'fAllowToGetHelp' 0; if ($st -eq 'Applied') { $changed = $true } }
    catch { Write-Log "Remote Assistance reg failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    try { $st = Set-ServiceSafe -Name 'RemoteRegistry' -StartupType Disabled -StopIfRunning; if ($st -eq 'Applied') { $changed = $true } elseif ($st -eq 'Failed') { $hardFailed = $true; $script:HadFailure = $true } }
    catch { Write-Log "RemoteRegistry service failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    if ($hardFailed) { return 'Failed' }
    if ($changed) { return 'Applied' }
    return 'AlreadyCompliant'
}

function Invoke-Tweaks {
    $changed = $false
    $hardFailed = $false

    $adv = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
    try { $st = Set-RegValue $adv 'HideFileExt' 0; if ($st -eq 'Applied') { $changed = $true } }
    catch { Write-Log "HideFileExt failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    try { $st = Set-RegValue 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Search' 'SearchboxTaskbarMode' 1; if ($st -eq 'Applied') { $changed = $true } }
    catch { Write-Log "SearchboxTaskbarMode failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }

    $cdm = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'
    foreach ($row in @(
        @{ Name='SoftLandingEnabled'; Value=0 },
        @{ Name='SubscribedContent-338389Enabled'; Value=0 },
        @{ Name='SystemPaneSuggestionsEnabled'; Value=0 }
    )) {
        try { $st = Set-RegValue $cdm $row.Name $row.Value; if ($st -eq 'Applied') { $changed = $true } }
        catch { Write-Log "$($row.Name) failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    }

    $pers = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
    foreach ($row in @(
        @{ Name='AppsUseLightTheme'; Value=0 },
        @{ Name='SystemUsesLightTheme'; Value=0 }
    )) {
        try { $st = Set-RegValue $pers $row.Name $row.Value; if ($st -eq 'Applied') { $changed = $true } }
        catch { Write-Log "$($row.Name) failed: $($_.Exception.Message)" ERROR; $hardFailed = $true; $script:HadFailure = $true }
    }

    if ($hardFailed) { return 'Failed' }
    if ($changed) { return 'Applied' }
    return 'AlreadyCompliant'
}
function Invoke-Apps {
    try {
        $Apps = @(
            'Brave.Brave',
            'RevoUninstaller.RevoUninstaller',
            'Guru3D.Afterburner',
            'Guru3D.RTSS'
        )
        $alreadyInstalled = -1978334963

        foreach ($app in $Apps) {
            $r = New-StepResult -Name "winget: $app"
            $oldEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $installed = @(& winget list --id $app --exact 2>&1)
                $listCode = $LASTEXITCODE
                # winget returns 0 only when the package is installed; regex is a secondary confirmation.
                if ($listCode -eq 0) {
                    $r.Status = 'AlreadyCompliant'
                } else {
                    $output = @(& winget install --id $app --silent --accept-package-agreements --accept-source-agreements 2>&1)
                    $code = $LASTEXITCODE
                    $output | ForEach-Object { Write-Log ([string]$_) }
                    if ($code -eq 0) {
                        $r.Status = 'Applied'
                    } elseif ($code -eq $alreadyInstalled) {
                        $r.Status = 'AlreadyCompliant'
                    } else {
                        $r.Status = 'Failed'
                        $r.Detail = "winget exit code $code"
                        $script:HadFailure = $true
                    }
                }
            } catch {
                $r.Status = 'Failed'
                $r.Detail = $_.Exception.Message
                $script:HadFailure = $true
            } finally {
                $ErrorActionPreference = $oldEap
            }
            $r.Ended = Get-Date
            $script:StepResults.Add($r)
            Write-Result $r
        }

        $nvidiaGpu = [bool](
            Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
                Where-Object { $_.PNPDeviceID -match 'VEN_10DE' }
        )
        $r = New-StepResult -Name 'NVIDIA driver'
        if (-not $nvidiaGpu) {
            $r.Status = 'AlreadyCompliant'
            $r.Detail = 'No NVIDIA GPU detected (VEN_10DE); skipped'
            $r.Ended = Get-Date
            $script:StepResults.Add($r); Write-Result $r
        } else {
            $oldEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
                    Where-Object { $_.PNPDeviceID -match 'VEN_10DE' } | Select-Object -First 1
                $pn = $gpu.PNPDeviceID
                $dev = ''; $ven = '10DE'; $subs = ''; $subv = ''
                if ($pn -match 'DEV_([0-9A-Fa-f]{4})') { $dev = $Matches[1].ToUpper() }
                if ($pn -match 'VEN_([0-9A-Fa-f]{4})') { $ven = $Matches[1].ToUpper() }
                if ($pn -match 'SUBSYS_([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})') { $subs = $Matches[1].ToUpper(); $subv = $Matches[2].ToUpper() }
                if ([string]::IsNullOrWhiteSpace($dev) -or [string]::IsNullOrWhiteSpace($subs)) {
                    throw "Could not parse NVIDIA PCI IDs from '$pn'"
                }
                $did = "$dev`_$ven`_$subs`_$subv"

                $isLaptop = $false
                try {
                    $enc = Get-CimInstance Win32_SystemEnclosure -ErrorAction SilentlyContinue
                    $laptopChassis = @(8,9,10,11,12,14,18,21,30,31,32)
                    if (@($enc.ChassisTypes) | Where-Object { $laptopChassis -contains $_ }) { $isLaptop = $true }
                } catch { }
                if (-not $isLaptop -and (Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue)) { $isLaptop = $true }

                $osBuild = (Get-CimInstance Win32_OperatingSystem).BuildNumber
                $query = (@{ dIDa = @($did); osC = '10.0'; osB = $osBuild; is6 = '1'; lg = '1033'; iLp = $(if ($isLaptop) { '1' } else { '0' }); prvMd = '0'; gcV = '3.18.0.94'; gIsB = '0'; dch = '1'; upCRD = '0'; isCRD = '0' } | ConvertTo-Json -Compress)

                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                $endpoint = 'https://gfwsl.geforce.com/nvidia_web_services/controller.gfeclientcontent.NG.php/com.nvidia.services.GFEClientContent_NG.getDispDrvrByDevid'
                $resp = Invoke-RestMethod -Uri "$endpoint/$query" -Headers @{ 'User-Agent' = 'NvBackend/36.0.0.0' }
                $url = $resp.DriverAttributes.DownloadURLAdmin
                $ver = $resp.DriverAttributes.Version
                if ([string]::IsNullOrWhiteSpace($url)) { throw 'Could not resolve NVIDIA driver download URL' }

                $installer = Join-Path $env:TEMP "NVIDIA_driver_$ver.exe"
                $ProgressPreference = 'SilentlyContinue'
                Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $installer
                $hash = (Get-FileHash -Path $installer -Algorithm SHA256).Hash
                Write-Log "NVIDIA driver $ver SHA-256: $hash  URL: $url"

                # -s silent; -n Display.Driver installs ONLY the display driver (no NVIDIA App);
                # -noreboot defers reboot. Exit: 0 ok, 1/3010 ok+reboot required.
                $out = & $installer -s -n Display.Driver -noreboot 2>&1
                $code = $LASTEXITCODE
                $out | ForEach-Object { Write-Log "NVIDIA driver: $([string]$_)" }
                if ($code -eq 0) {
                    $r.Status = 'Applied'
                    $r.Detail = "Installed NVIDIA driver $ver (Display.Driver only)"
                } elseif ($code -in @(1,3010)) {
                    $r.Status = 'Applied'
                    $r.Detail = "Installed NVIDIA driver $ver (reboot required)"
                    $script:RebootRequired = $true
                } else {
                    throw "NVIDIA driver installer exited with code $code"
                }
            } catch {
                $r.Status = 'Failed'
                $r.Detail = "$($_.Exception.Message); install manually from https://www.nvidia.com/en-us/drivers/"
                $script:HadFailure = $true
            } finally {
                $ErrorActionPreference = $oldEap
                $r.Ended = Get-Date
                $script:StepResults.Add($r); Write-Result $r
            }
        }

    } catch {
        Write-Log "FATAL in Invoke-Apps: $($_.Exception.Message)" ERROR
        $script:HadFailure = $true
    }
}
function Invoke-Activation {
    try {
        $licenses = @(Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL" |
            Where-Object { $_.Name -like 'Windows*' })
        foreach ($candidate in $licenses) {
            Write-Log "License: $($candidate.Name) edition=$($candidate.EditionID) status=$($candidate.LicenseStatus) addon=$($candidate.LicenseIsAddon) grace=$($candidate.GracePeriodRemaining) key=$($candidate.PartialProductKey)"
        }

        $lic = $licenses | Where-Object { -not $_.LicenseIsAddon } | Select-Object -First 1
        $isLicensed = ($null -ne $lic -and $lic.LicenseStatus -eq 1)
        $editionBefore = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').EditionID

        if ($isLicensed) {
            return 'AlreadyCompliant'
        }

        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        $masPath = Join-Path $env:TEMP 'mas-aio.ps1'
        Invoke-WebRequest -Uri 'https://get.activated.win' -UseBasicParsing -OutFile $masPath
        $masHash = (Get-FileHash -Path $masPath -Algorithm SHA256).Hash
        Write-Log "MAS SHA-256: $masHash"

        $wrapper = Join-Path $env:TEMP 'mas-hwid-wrapper.ps1'
        $masPathEscaped = $masPath -replace "'", "''"
        @"
`$ErrorActionPreference = 'Stop'
`$script = Get-Content -Raw -LiteralPath '$masPathEscaped'
& ([ScriptBlock]::Create(`$script)) /HWID-NoEditionChange
"@ | Set-Content -LiteralPath $wrapper -Encoding UTF8

        $masExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $oldEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $masOutput = & $masExe -NoProfile -ExecutionPolicy Bypass -File $wrapper 2>&1
            $masCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $oldEap
        }
        $masOutput | ForEach-Object { Write-Log "MAS: $([string]$_)" }
        if ($masCode -ne 0) { throw "MAS exited with code $masCode" }

        $licensesAfter = @(Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL" |
            Where-Object { $_.Name -like 'Windows*' })
        $licAfter = $licensesAfter | Where-Object { -not $_.LicenseIsAddon } | Select-Object -First 1
        $editionAfter = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').EditionID

        if ($null -eq $licAfter -or $licAfter.LicenseStatus -ne 1 -or $licAfter.LicenseIsAddon -or $editionAfter -ne $editionBefore) {
            throw "Post-activation license check failed (status=$($licAfter.LicenseStatus), addon=$($licAfter.LicenseIsAddon), editionBefore=$editionBefore, editionAfter=$editionAfter)"
        }

        Write-Log "Post-activation: edition=$editionAfter status=$($licAfter.LicenseStatus) grace=$($licAfter.GracePeriodRemaining) key=$($licAfter.PartialProductKey)"
        return 'Applied'
    } catch {
        Write-Log "Activation failed: $($_.Exception.Message)" ERROR
        Write-Log 'Manual fallback: irm https://get.activated.win | iex  (choose HWID)' WARN
        $script:ActivationFailed = $true
        return 'Failed'
    }
}

function Get-ExitCode {
    if ($script:HadFailure) { return 1 }
    if ($script:ActivationFailed) { return 3 }
    if ($script:HadWarning -or $script:LogWriteFailed) { return 2 }
    return 0
}

function Show-Summary {
    Write-Log '--- Summary ---'
    foreach ($r in $script:StepResults) { Write-Result $r }
    $failed = @($script:StepResults | Where-Object { $_.Status -eq 'Failed' })
    if ($failed.Count -gt 0) {
        Write-Log ("Failed steps: " + ($failed.Name -join ', ')) WARN
    }
    if ($script:RebootRequired) { Write-Log 'Reboot required.' WARN }
    if ($script:ActivationFailed) { Write-Log 'Manual fallback: irm https://get.activated.win | iex (choose HWID)' WARN }
    if ($script:LogPath) { Write-Log "Log: $script:LogPath" }
}

function Main {
    $code = 1
    try {
        Initialize-Logging

        if ($script:InvocationArgs.Count -gt 0) {
            Write-Log 'windows-setup.ps1 takes no arguments.' ERROR
            return 1
        }

        $pre = Invoke-Preflight
        if ($pre.Refused) {
            Show-Summary
            return 1
        }

        $script:BeforeState = Get-StateSnapshot

        Invoke-Mutation 'Core system' { Invoke-CoreSystem }
        Invoke-Debloat
        Invoke-Mutation 'Hardening' { Invoke-Hardening }
        Invoke-Mutation 'Power/Game' { Invoke-PowerAndGame }
        Invoke-Mutation 'Tweaks' { Invoke-Tweaks }

        Invoke-Apps
        Invoke-Mutation 'Activation' { Invoke-Activation }

        $script:AfterState = Get-StateSnapshot
        $code = Get-ExitCode
        Write-StateSummary -Before $script:BeforeState -After $script:AfterState -Results $script:StepResults
        Show-Summary
        return $code
    } catch {
        Write-Log "FATAL: $($_.Exception.Message)" FATAL
        Write-Result ([pscustomobject]@{
            Name = 'Fatal'; Status = 'Failed'; Detail = $_.Exception.Message; Ended = (Get-Date)
        })
        $code = 1
        return $code
    } finally {
        Write-Log "Exit code: $code"
    }
}

exit (Main)
