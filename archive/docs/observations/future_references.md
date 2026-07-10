# Future References — Scratchpad (DEPRECATED)

Content promoted to `docs/user_reference.md` and `docs/troubleshooting.md`.
Keep this file only if new draft content is needed.

---

## Troubleshooting Reference (Draft)

Common failure modes, diagnosis commands, and fixes.

### Categories to cover

- **WiFi** — Intel AX201 unrecoverable crash (full power cycle), firmware drift, kernel update breaks iwlwifi
- **dnsmasq** — Service not running, config generation failed, port 53 already in use, deny overrides not working
- **nftables** — Rules not loading, locked mode not blocking DNS, container traffic affected (shouldn't be)
- **Browser policies** — Policy JSON syntax errors, bookmarks not appearing, wrong mode showing in brave://policy
- **Seal/recovery** — `tle` binary missing, drand network unreachable, timelock not expired, `unseal` fails
- **Allowlist** — Domain not resolving after add, `clear-session` not taking effect, `lock` says empty
- **Container** — Podman pull fails (Docker Hub domain not in allowlist), container can't resolve DNS
- **Verify.sh** — Specific check failures and what each means
- **Boot** — System won't boot after seal (do we have Timeshift?), GPU driver issues, initramfs rebuild needed

---

## User Utilities & Aliases Reference (Draft)

### CLI utilities (in PATH via `/usr/local/bin/`)

| Command | Source | Purpose |
|---------|--------|---------|
| `check-firmware` | `.config/scripts/check-firmware-drift.sh` | Check iwlwifi firmware + microcode drift |
| `help` | `.config/scripts/help.sh` | Show keybindings reference via glow |
| `unseal` | `.config/scripts/unseal.sh` | Decrypt timelock-sealed credentials |

### Allowlist commands (root, via `/opt/allowlist/allowlist.sh`)

| Command | Purpose |
|---------|---------|
| `allowlist lock` | Enable DNS whitelist |
| `allowlist unlock` | Disable DNS whitelist |
| `allowlist toggle` | Switch between locked/unrestricted |
| `allowlist status` | Show current mode + domain counts |
| `allowlist search <pattern>` | Search domains across all sections |
| `allowlist list [--infra\|--base\|--session]` | List domains by section |
| `allowlist clear-session` | Remove all session domains |
| `allowlist verify` | Run full lockdown verification |
| `allowlist seal` | Generate random root password, encrypt with timelock, reboot |

### Bash aliases

| Alias | Expands to | Purpose |
|-------|------------|---------|
| `docker` | `podman` | Podman replaces Docker |
| `reboot` | `sudo systemctl reboot` | Reboot via restricted sudo |
| `poweroff` | `sudo systemctl poweroff` | Power off via restricted sudo |
| `suspend` | `sudo systemctl suspend` | Suspend via restricted sudo |

### Keybindings (Sway)

See `keybindings.md` for full table.
