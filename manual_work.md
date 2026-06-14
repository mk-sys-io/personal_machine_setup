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

7. [LOCK] — activate dnsmasq whitelist + nftables DNS block:

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

9. [LOCK AGAIN] — re-lock for the focus session:

```bash
/opt/allowlist/allowlist.sh lock
```

---

10. [SEAL — optional] — encrypts `~/.config/recovery-credentials` with a timelock, then **deletes** the plaintext file:

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
  --dns 1.1.1.1 \
  alpine sh -c "
    apk add -q curl tar
    curl -fsSL -o /tmp/tlock.tar.gz \
      https://github.com/drand/tlock/releases/download/v1.2.0/tlock_1.2.0_linux_amd64.tar.gz
    tar xzf /tmp/tlock.tar.gz -C /usr/bin tle
    /usr/bin/tle -d -o /host-config/recovery-credentials /host-config/sealed-credentials
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
