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
shutdown -h now
```

Then unplug power, wait 30+ seconds, plug in, and boot.

### Diagnosis

```bash
rfkill list                   # check if soft/hard blocked
iw dev wlp0s20f3 link         # check connection state
dmesg | grep -i iwlwifi       # check driver/firmware errors
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
apt-mark hold firmware-iwlwifi
# or
apt-mark hold linux-image-6.12.86+deb13-amd64
```

Boot the older kernel from the GRUB advanced menu to recover.

After recovery, run `check-firmware` to confirm loaded firmware matches
the pinned package version.

---

## Nouveau GSP Firmware Regression

If a kernel update breaks Nouveau GSP firmware loading, the GPU falls
back to basic mode (no reclocking, higher power consumption).

### Diagnosis

```bash
dmesg | grep -i 'nouveau.*gsp'
journalctl -k --no-pager | grep -i 'nouveau.*gsp'
cat /proc/cmdline | tr ' ' '\n' | grep nouveau
```

Expected: GSP firmware loaded, `nouveau.config=NvGspRm=1` in cmdline.

### Recovery

If the kernel param is missing, verify `/etc/default/grub.d/99-nouveau-gsp.cfg`
exists and re-run `sudo update-grub`. If the file is intact but the new
kernel broke GSP support, boot the previous kernel from GRUB advanced menu.
