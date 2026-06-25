# Manual Work

This document contains only steps that must be performed manually
after running `install.sh`. If a step can be automated, it belongs
in the installer or a script — not here.

**In scope:** one-time setup, verification, and configuration tasks
that require human judgment (setting passwords, checking browser
policies, deciding whether to seal).

**Not in scope:** recurring failure modes (see troubleshooting.md),
reference information, design rationale.

---

## Browser policy check

After the first graphical login, open `brave://policy` (Brave) and
`chrome://policy` (Chrome/Chromium). Check "Show policies with no value set".

Look for errors — any entry showing **Status: Error** or an incorrect **Value**
type (e.g. string instead of boolean, or vice versa) means the policy JSON is
malformed. Also watch for **Status: Not set** on expected policies — that means
the file wasn't picked up at the expected path.

Common correct values: `false` (boolean), `true` (boolean), `1` (integer),
`0` (integer), `"off"` (string). Each policy's expected type is determined by
its Chromium schema — a mismatch produces a red error state.

If anything is wrong, re-run `sudo /opt/allowlist/generate-policies.sh`,
restart the browser, and re-check.

---

## Pre-lockdown verification

Run these checks after the `install.sh` reboot to confirm the time-bomb fixes
are working before proceeding with lockdown.

### 1. Firmware drift (iwlwifi + microcode)

```bash
check-firmware
```

**Expected result:** Both checks show PASS. No WARN or ERROR lines.
If microcode shows SKIP, confirm `intel-microcode` is installed
(`dpkg -l intel-microcode`).

---

### 2. Nouveau GSP firmware

```bash
dmesg | grep -i 'nouveau.*gsp'
```

If `dmesg` is restricted, use `sudo dmesg` or fall back to:
```bash
journalctl -k --no-pager | grep -i 'nouveau.*gsp'
```

**Expected result:** Output showing GSP firmware loaded,
e.g. `nouveau: loading NVIDIA GSP firmware`.

If empty, verify the kernel param was applied:
```bash
cat /proc/cmdline | tr ' ' '\n' | grep nouveau
```
Should show `nouveau.config=NvGspRm=1`. If not, re-run `install.sh`
or add it manually to `/etc/default/grub.d/99-nouveau-gsp.cfg` and run
`sudo update-grub`.

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

3. [EDIT MOBILE CREDENTIALS] — add your mobile/account passwords:

```bash
nano ~/.config/seal/mobile.credentials
```

Add passwords as `key=value` lines, one per line. Example:
```
mobile_password=mysecret
bank_password=anothersecret
```

`system.credentials` is auto-managed by `seal -s`.

---

3a. [SEAL MOBILE CREDENTIALS] — encrypt with a timelock:

```bash
sem
```

`sem` encrypts `mobile.credentials` to `mobile.sealed`, shreds the plaintext,
clears clipboard, and wipes shell history. Choose a duration when prompted.

Recover with `unseal -m` after the timelock expires.

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

This writes `root_password=...` back to `~/.config/seal/recovery-credentials`
and copies the root password to the clipboard automatically (copyq, fallback wl-copy).
Then:

```bash
su -
```

Paste with `$mod+V` (Sway) at the password prompt, or copy manually from the
terminal output. Then manage the allowlist:

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

### Mobile credentials

After the timelock expires, decrypt and display:

```bash
unseal -m
```

The credentials are written to `~/.config/seal/mobile.credentials`.
Select and copy the password from the terminal output.
