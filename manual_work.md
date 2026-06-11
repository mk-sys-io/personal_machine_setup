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

5. [CREATE ENV FILE] — Go to https://my.nextdns.io, sign up/log in, create a configuration, and copy the 6-character **Configuration ID**.

Write it to `/opt/allowlist/env`:

```bash
sudo tee /opt/allowlist/env <<< 'NEXTDNS_CONFIG_ID="your-6-char-id"'
```

Make it root-owned:

```bash
sudo chown root:root /opt/allowlist/env
```

```bash
sudo chmod 600 /opt/allowlist/env
```

---

6. [REMOVE SUDO] — after this point, every step runs as root via `su -`:

```bash
sudo gpasswd -d mike sudo
```

---

### As root (via su -)

7. [LOG IN AS ROOT] — enter the root password you set in step 2:

```bash
su -
```

---

8. [SET UP NEXTDNS] — install and start the service:

```bash
nextdns install -profile "your-6-char-id"
```

```bash
systemctl enable --now nextdns
```

Verify DNS resolution:

```bash
nextdns status
```

```bash
ping -c 2 github.com
```

Regenerate browser policies to pick up the real NextDNS ID:

```bash
/opt/allowlist/generate-policies.sh
```

Verify DoH is active at `chrome://policy` (Brave) or `about:policies` (Firefox).

---

9. [LOCK] — activate URL filtering + nftables DNS block:

```bash
/opt/allowlist/allowlist.sh lock
```

```bash
/opt/allowlist/allowlist.sh verify
```

---

10. [UNLOCK] — confirm you can toggle back:

```bash
/opt/allowlist/allowlist.sh unlock
```

```bash
/opt/allowlist/allowlist.sh verify
```

---

11. [LOCK AGAIN] — re-lock for the focus session:

```bash
/opt/allowlist/allowlist.sh lock
```

---

12. [SEAL — optional] — encrypts `~/.config/recovery-credentials` with a timelock, then **deletes** the plaintext file:

```bash
/opt/allowlist/allowlist.sh seal
```

After this, the only way to recover the root password is via the timelock.

---

### Recovery (after sudo removal + seal)

If you removed sudo and sealed the credentials, the only way back is via podman. Wait for the timelock duration to expire, then:

```bash
podman run --rm \
  -v ~/.config:/host-config:rw \
  alpine sh -c "
    apk add -q curl tar
    curl -fsSL -o /tmp/tlock.tar.gz \
      https://github.com/drand/tlock/releases/download/v1.2.0/tlock_1.2.0_linux_amd64.tar.gz
    tar xzf /tmp/tlock.tar.gz -C /usr/local/bin tle
    /usr/local/bin/tle -d -o /host-config/recovery-credentials /host-config/sealed-credentials
  "
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

```bash
/opt/allowlist/allowlist.sh seal
```

---

## DNS-leak Prevention (kernel firewall)

When `allowlist lock` is active, nftables blocks all DNS traffic (UDP/TCP ports 53, 853) from user `mike` that goes to non-NextDNS IPs.

This means CLI tools run by user `mike` (`dig`, `nslookup`, `curl`, `ping`) **will fail to resolve DNS** when locked. Root (via `su -`) is unaffected.

**Browser DNS is unaffected** — Brave and Firefox use DoH directly on port 443, not the system resolver.

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
