# User Reference

Inventory of every CLI utility, alias, and command available to user mike.

---

## CLI Utilities

Deployed from `.config/scripts/` to `/usr/local/bin/` by `install.sh`.
All are in default `$PATH`.

| Command | Source | Purpose |
|---------|--------|---------|
| `check-firmware` | `.config/scripts/check-firmware-drift.sh` | Check loaded iwlwifi firmware vs package files, intel-microcode revision |
| `help` | `.config/scripts/help.sh` | Show keybindings reference via `glow --pager` in a foot terminal |
| `unseal` | `.config/scripts/unseal.sh` | Decrypt `~/.config/seal/sealed-credentials` with `tle -d` |

## Allowlist Commands

All commands require root. Run via `su -` after sudo removal.

| Command | Purpose |
|---------|---------|
| `allowlist lock` | Enable DNS whitelist — only allowlisted domains resolve |
| `allowlist unlock` | Disable DNS whitelist — all domains resolve |
| `allowlist toggle` | Switch between locked and unrestricted |
| `allowlist status` | Show current mode and per-section domain counts |
| `allowlist search <pattern>` | Search for domains matching pattern across all sections |
| `allowlist list [--infra\|--base\|--session]` | List domains by section (omit flag for all) |
| `allowlist clear-session` | Remove all session domains and redeploy |
| `allowlist verify` | Run full lockdown verification (10 checks) |
| `allowlist seal` | Generate random root password, encrypt with timelock, lock, reboot |

## Bash Aliases

Defined in `.config/bashrc`, appended to `~/.bashrc` by `install.sh`.

| Alias | Expands to | Purpose |
|-------|------------|---------|
| `docker` | `podman` | Podman replaces Docker |
| `reboot` | `sudo systemctl reboot` | Reboot via restricted sudo |
| `poweroff` | `sudo systemctl poweroff` | Power off via restricted sudo |
| `suspend` | `sudo systemctl suspend` | Suspend via restricted sudo |

## Restricted Sudo Commands

Defined in `.config/sudoers/99-mike-tools`.
No full sudo — these are the only commands mike can run as root.

| Command | Purpose |
|---------|---------|
| `sudo apt update` | Refresh package index |
| `sudo apt upgrade` | Upgrade all packages |
| `sudo apt install --reinstall <pkg>` | Reinstall a specific package |
| `sudo systemctl status <svc>` | Check service status |
| `sudo systemctl is-active <svc>` | Check if service is running |
| `sudo systemctl is-enabled <svc>` | Check if service is enabled |
| `sudo systemctl list-units` | List all systemd units |
| `sudo journalctl` | View system logs |
| `sudo systemctl reboot` | Reboot the system |
| `sudo systemctl poweroff` | Power off the system |
| `sudo systemctl suspend` | Suspend to RAM |
| `sudo systemctl restart NetworkManager` | Recover WiFi after NM crash |
| `sudo systemctl restart dnsmasq` | Recover DNS proxy (needed by seal/unseal) |
| `sudo systemctl restart nftables` | Recover firewall (re-applies lockdown rules) |
| `sudo ip link set <dev> up\|down` | Toggle interface after suspend/AX201 crash |
| `sudo nft list ruleset` | Inspect firewall rules (read-only) |
| `sudo rfkill unblock wifi` | Unblock WiFi radio after soft-block |
| `sudo dmesg` | View kernel log (WiFi/driver errors) |
| `sudo timeshift` | Create/list/restore snapshots |

## Waybar Modules

Displayed in the top status bar (left to right).

| Module | Source | What it shows |
|--------|--------|---------------|
| Workspaces | sway/workspaces | Active workspace name |
| Clock | date + format | Current date and time |
| Volume | `.config/waybar/scripts/volume.sh` | Volume % or MUTED |
| Brightness | `.config/waybar/scripts/brightness.sh` | Backlight % |
| Network | `.config/waybar/scripts/network.sh` | WiFi ESSID + signal % or IP |
| NumLock | `.config/waybar/scripts/numlock.sh` | NUM or num |
| Battery | sysfs | Charge % and status |

## Keybindings

See `keybindings.md` for the complete reference table.
View at any time with `help` (opens `glow --pager`).
