# Kill switch (one-way: Linux → Windows)

Linux controls whether Windows has internet access. One-way: Linux writes a flag file, Windows reads it at boot. Not real-time (change takes effect on next Windows boot).

## Mechanism

1. Linux writes `allow` or `deny` to `/mnt/windows/killswitch.flag` (on the Windows NTFS partition).
2. Windows scheduled task (at boot, running as SYSTEM) reads the flag file.
3. If `deny`: disable the network adapter or block outbound traffic via Windows Firewall.
4. If `allow`: enable the network adapter or allow outbound traffic.

## Linux side (flag writer)

```bash
# Allow internet
echo "allow" > /mnt/windows/killswitch.flag

# Deny internet
echo "deny" > /mnt/windows/killswitch.flag
```

Linux has direct read/write access to the Windows NTFS partition via ntfs3/ntfs-3g (see [file-transfer.md](file-transfer.md)).

## Windows side (scheduled task)

PowerShell script (`killswitch-check.ps1`), triggered by a scheduled task at boot (SYSTEM, highest privileges):

```powershell
$flag = Get-Content "C:\killswitch.flag" -ErrorAction SilentlyContinue
if ($flag -eq "deny") {
    # Disable all network adapters
    Get-NetAdapter | Disable-NetAdapter -Confirm:$false
    # Or: block outbound via firewall
    # New-NetFirewallRule -DisplayName "KillSwitch-Block" -Direction Outbound -Action Block -Enabled True
} else {
    Get-NetAdapter | Enable-NetAdapter -Confirm:$false
}
```

## Limitations

- Change takes effect on next Windows boot, not in real-time.
- Windows must be fully shut down (not hibernated) for Linux to write the flag cleanly.
- Fast Startup must be disabled (already planned).
- The flag file path must be consistent between Linux and Windows (same NTFS partition, same relative path).

## Default

TBD (open question): should Windows start with internet allowed or denied on first boot?

## No shared partition needed

Linux writes directly to the Windows NTFS filesystem. No separate shared partition required.