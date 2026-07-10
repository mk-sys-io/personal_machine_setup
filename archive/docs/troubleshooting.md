# Troubleshooting

This document covers only recurring failure modes that cannot be
permanently fixed in code. If the root cause can be patched in
install.sh, a script, or a config file, it belongs there — not here.

**In scope:** hardware-level issues, kernel/boot behavior changes
across updates, any issue requiring manual intervention every time.

**Not in scope:** configuration errors fixable by re-running install.sh,
missing binaries, policy syntax errors, dnsmasq config issues.

---

## Intel AX201 WiFi Crash

The Intel AX201 adapter can enter an unrecoverable hardware state after a
firmware or kernel update. A warm reboot does not fix it — only a full
power cycle clears the crashed firmware inside the CNVi controller.

### Recovery

```
sudo systemctl poweroff
```

Then unplug power, wait 30+ seconds, plug in, and boot.

### Diagnosis

```bash
sudo rfkill list               # check if soft/hard blocked
iw dev wlp0s20f3 link         # check connection state
sudo dmesg | grep -i iwlwifi  # check driver/firmware errors
```

### Prevention

This has no software fix — the firmware crashes at the hardware level.
Always try a full power cycle before debugging further.

---

## Firmware/Kernel Update Breaks WiFi

If WiFi breaks after a system update, the firmware or kernel image may be
incompatible with the AX201 on this hardware.

### Recovery

Pin the working packages:

```bash
sudo apt-mark hold firmware-iwlwifi
# or
sudo apt-mark hold linux-image-6.12.86+deb13-amd64
```

Boot the older kernel from the GRUB advanced menu to recover.

After recovery, run `check-firmware` to confirm loaded firmware matches
the pinned package version.

---

## WiFi Not Connecting / No Networks Detected

If WiFi is up but not connecting (or not detecting any SSIDs), the
issue is usually NetworkManager or the interface state rather than a
full firmware crash.

### Diagnosis

```bash
sudo dmesg | grep -i iwlwifi       # driver/firmware errors
sudo rfkill list                    # soft/hard blocked? (sudo rfkill unblock wifi if soft-blocked)
iw dev wlp0s20f3 link               # already connected to something?
iw dev wlp0s20f3 scan               # see any APs?
sudo nft list ruleset               # firewall blocking anything?
```

### Recovery

```bash
# Restart NetworkManager (resolves most transient failures)
sudo systemctl restart NetworkManager

# If NM restart doesn't help, toggle the interface
sudo ip link set wlp0s20f3 down
sudo ip link set wlp0s20f3 up

# If DNS is broken but WiFi connected, restart the proxy
sudo systemctl restart dnsmasq

# If firewall rules are suspect, restart nftables
sudo systemctl restart nftables
```

All commands work via the restricted sudo entries in
`/etc/sudoers.d/99-mike-tools` — no full sudo needed. If none of these
restore connectivity, the AX201 may need a full power cycle (see above).

---

## Nouveau GSP Firmware Regression

If a kernel update breaks Nouveau GSP firmware loading, the GPU falls
back to basic mode (no reclocking, higher power consumption).

### Diagnosis

```bash
sudo dmesg | grep -i 'nouveau.*gsp'
sudo journalctl -k --no-pager | grep -i 'nouveau.*gsp'
cat /proc/cmdline | tr ' ' '\n' | grep nouveau
```

Expected: GSP firmware loaded, `nouveau.config=NvGspRm=1` in cmdline.

### Recovery

If the kernel param is missing, verify `/etc/default/grub.d/99-nouveau-gsp.cfg`
exists and re-run `sudo update-grub`. If the file is intact but the new
kernel broke GSP support, boot the previous kernel from GRUB advanced menu.
