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

3. [CREATE RECOVERY FILE] — save critical secrets (root password + any other info):

```bash
echo 'root_password=<your-root-password>' > ~/.config/seal/recovery-credentials
```

```bash
chmod 600 ~/.config/seal/recovery-credentials
```

This file can contain multiple lines with any critical info. `seal` appends the
new random root password to it (preserving existing content), then encrypts the
entire file with `tle` and permanently deletes the plaintext copy.

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

### Testing the root password change (optional, first-time only)

The `seal` command generates a random root password, changes root to it, appends
it to `recovery-credentials`, encrypts the file with a timelock, locks the
system, and reboots. To test the full cycle:

1. **Take a timeshift snapshot** — rollback point:

   ```bash
   sudo timeshift --create --comments "pre-seal-test"
   ```

2. **Run seal** — choose a short duration (e.g. 30 minutes):

   ```bash
   /opt/allowlist/allowlist.sh seal
   ```

   The system locks and reboots.

3. **Wait for the timelock to expire**, then decrypt:

   ```bash
   unseal
   ```

   This prints the decrypted password.

4. **Verify the password works**:

   ```bash
   su -
   ```

5. **Restore** to revert if anything failed:

   ```bash
   sudo timeshift --restore
   ```

   Select the `pre-seal-test` snapshot. This restores `/etc/shadow` (the root
   password hash). Note: timeshift **excludes `/home/`** by default, so
   `~/.config/seal/recovery-credentials` must be recreated:

   ```bash
   rm ~/.config/seal/recovery-credentials
   echo 'root_password=<your-original-password>' > ~/.config/seal/recovery-credentials
   chmod 600 ~/.config/seal/recovery-credentials
   ```

---

### Seal — The point of no return

**9. [SEAL]** — checks drand network, generates a random root password, changes
root to it, appends it to `recovery-credentials`, encrypts the file with a
timelock, then **permanently deletes** the plaintext.

**⚠️ CRITICAL: You MUST be UNLOCKED before sealing.** `tle` needs unrestricted DNS to reach the drand timelock network.

```bash
/opt/allowlist/allowlist.sh seal
```

After this, the root password cannot be recovered **until the timelock duration expires**. The only recovery path is the `unseal` command below, and it will not work before the set duration is up.

Verify the sealed file exists:
```bash
ls -la ~/.config/seal/sealed-credentials
```

---

### Recovery (after sudo removal + seal)

If you removed sudo and sealed the credentials, you must wait for the timelock duration to expire, then decrypt. The drand API domains are whitelisted in the allowlist, so decryption works even when locked.

```bash
unseal    # /usr/local/bin/unseal — deployed by install.sh
```

This writes `root_password=...` back to `~/.config/seal/recovery-credentials`. Then:

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


