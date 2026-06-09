# Manual Work

Steps that cannot be automated and must be performed manually after running `install.sh`.

---

## CopyQ: Enable auto-paste on Wayland

CopyQ cannot auto-paste (Enter → paste to previous window) on Wayland because the protocol blocks synthetic keystrokes. To enable it, import the "Wayland Support" command:

1. Open CopyQ (`$mod+v` or `copyq toggle`)
2. **File → Commands → Import**
3. Wait for the command list to load
4. Search for **"Wayland Support"**
5. Click install/import

This command uses `ydotool` (installed automatically by `install.sh`, daemon started as `exec ydotoold` in Sway config) to inject keystrokes. Without it, selecting an item copies it to clipboard — paste manually with `Ctrl+V`.

**When:** Once, after first CopyQ launch.

---

## NextDNS Setup

Requires a NextDNS account (free tier works). This cannot be fully automated because the configuration ID must be obtained from the website.

1. Go to https://my.nextdns.io and sign up/log in
2. Create a new configuration (or use the auto-created one in the dashboard)
3. Copy the 6-character **Configuration ID** from the setup page
4. Write it to `~/linux_setup/.config/env`:
   ```
   NEXTDNS_CONFIG_ID="your-6-char-id"
   ```
5. Run the NextDNS service installation:
   ```bash
   source ~/linux_setup/.config/env
   sudo nextdns install -profile "$NEXTDNS_CONFIG_ID"
   sudo systemctl enable --now nextdns
   ```
6. Verify DNS resolution works:
   ```bash
   nextdns status
   ping -c 2 github.com
   ```
7. Regenerate browser policies to pick up the real NextDNS config ID
   (the initial `install.sh` ran before `.config/env` existed):
   ```bash
   allowlist unlock    # or `allowlist lock` if you want URL filtering
   ```
8. Verify DoH is active in browser policies:
   - Brave:    `chrome://policy`
   - Firefox:  `about:policies`

**When:** Once, after running `install.sh`.

---

## General setup

1. **Log out and back in** (or restart Sway via `$mod+Shift+e` then log in again) after `install.sh` completes — this ensures Sway reads the updated config and all services start fresh.

---

## DNS-leak Prevention (kernel firewall)

When `allowlist lock` is active, nftables blocks all DNS traffic (UDP/TCP ports 53, 853) from user `mike` that goes to non-NextDNS IPs.

This means CLI tools run directly by the user (`dig`, `nslookup`, `curl`, `ping`) **will fail to resolve DNS** when locked — their queries to the router (192.168.1.1) are dropped. Use `sudo` for CLI tools that need DNS, or configure the system resolver to `127.0.0.1` (the NextDNS local proxy).

**Browser DNS is unaffected** — Brave and Firefox use DoH directly on port 443, not the system resolver.

**When:** After running `allowlist lock`.
