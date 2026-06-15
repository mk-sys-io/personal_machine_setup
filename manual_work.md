# Manual Work

Steps that cannot be automated and must be performed manually after running `install.sh`.

---

## Post-install Flow

After `install.sh` completes, follow these steps in order:

---

### As mike (with sudo)

1. [REBOOT/LOG OUT] — restart Sway (`$mod+Shift+e` then log in) or reboot so Sway reads the updated configs and all services start fresh.

---

2. [SET ROOT PASSWORD] — needed for recovery / `su -`:

```bash
sudo passwd root
```

---

3. [CREATE RECOVERY FILE] — save the root password for timelock sealing:

```bash
echo 'root_password=<your-root-password>' > ~/.config/recovery-credentials
```

```bash
chmod 600 ~/.config/recovery-credentials
```

This file is read once by `seal`, then encrypted with `tle` and permanently deleted.

---

4. [REVIEW ALLOWLIST] — check default domains:

```bash
sudo /opt/allowlist/allowlist.sh list
```

Add a domain:

```bash
sudo /opt/allowlist/allowlist.sh add <domain>
```

Remove a domain:

```bash
sudo /opt/allowlist/allowlist.sh remove <domain>
```

---

5. [REMOVE SUDO] — after this point, every step runs as root via `su -`:

```bash
sudo gpasswd -d mike sudo
```

---

### As root (via su -)

6. [LOG IN AS ROOT] — enter the root password you set in step 2:

```bash
su -
```

---

### Test the allowlist (optional, recommended)

These steps verify the lock/unlock cycle works. Seal requires an unlocked
system, so you must test now and unlock before proceeding.

7. [LOCK — test] — activate the allowlist:

```bash
/opt/allowlist/allowlist.sh lock
```

```bash
/opt/allowlist/allowlist.sh verify
```

---

8. [UNLOCK] — confirm you can toggle back:

```bash
/opt/allowlist/allowlist.sh unlock
```

```bash
/opt/allowlist/allowlist.sh verify
```

---

### Seal — The point of no return

**9. [SEAL]** — encrypts `~/.config/recovery-credentials` with a timelock, then **permanently deletes** the plaintext file.

**⚠️ CRITICAL: You MUST be UNLOCKED before sealing.** `tle` needs unrestricted DNS to reach the drand timelock network.

```bash
/opt/allowlist/allowlist.sh seal
```

After this, the root password cannot be recovered **until the timelock duration expires**. The only recovery path is the podman command below, and it will not work before the set duration is up.

Verify the sealed file exists:
```bash
ls -la ~/.config/sealed-credentials
```

---

### Recovery (after sudo removal + seal)

If you removed sudo and sealed the credentials, you must wait for the timelock duration to expire, then decrypt. The drand API domains are whitelisted in the allowlist, so decryption works even when locked.

```bash
/usr/local/bin/tle -d \
  -o ~/.config/recovery-credentials \
  ~/.config/sealed-credentials
```

This writes `root_password=...` back to `~/.config/recovery-credentials`. Then:

```bash
su -
```

Enter the recovered root password, then manage the allowlist:

```bash
/opt/allowlist/allowlist.sh unlock
```

```bash
/opt/allowlist/allowlist.sh lock
```

If you need to re-seal, you must be unlocked first:

```bash
/opt/allowlist/allowlist.sh unlock
/opt/allowlist/allowlist.sh seal
```

---

## WiFi Troubleshooting (Intel AX201)

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
If WiFi breaks after an update, hold the working packages:
```bash
apt-mark hold firmware-iwlwifi
# or
apt-mark hold linux-image-6.12.86+deb13-amd64
```
Boot the older kernel from the GRUB advanced menu to recover.

---

## DNS-leak Prevention (kernel firewall)

When `allowlist lock` is active, nftables blocks all DNS traffic (UDP/TCP ports 53, 853) from user `mike` to any external IP. Only loopback traffic to `127.0.0.1:53` is allowed — this is where dnsmasq listens.

This means CLI tools run by user `mike` (`dig`, `nslookup`, `curl`, `ping`) **will fail to resolve DNS** when locked. Root (via `su -`) is unaffected because nftables only targets UID 1000.

**Browser DNS is also forced through dnsmasq** — enterprise policies disable DoH and the built-in async DNS client. All browser DNS goes through the OS resolver → `127.0.0.1:53` → dnsmasq → `1.1.1.1`.

---

## CopyQ (Optional)

CopyQ cannot auto-paste (Enter → paste to previous window) on Wayland because the protocol blocks synthetic keystrokes. To enable it, import the "Wayland Support" command:

1. Open CopyQ (`$mod+v` or `copyq toggle`)
2. **File → Commands → Import**
3. Wait for the command list to load
4. Search for **"Wayland Support"**
5. Click install/import

This command uses `ydotool` (installed automatically by `install.sh`, daemon started as `exec ydotoold` in Sway config) to inject keystrokes. Without it, selecting an item copies it to clipboard — paste manually with `Ctrl+V`.

**When:** Once, after first CopyQ launch.
