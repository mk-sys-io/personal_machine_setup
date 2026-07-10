# Phase 4 — Seal & Sudo Removal

## Overview

Phase 4 makes the allowlist lockdown irreversible from user `mike`'s
perspective. Recovery is possible directly on the host after a timelock
expires — the drand API domains are whitelisted in the allowlist so
`tle` works even when locked (see [001] in decisions.md).

## The `seal` Command

```bash
sudo /opt/allowlist/allowlist.sh seal
```

Reads `~/.config/seal/recovery-credentials` (must exist — user creates it beforehand):

1. **Checks** `tle` is installed (`~/go/bin/tle` or `/usr/local/bin/tle`)
2. **Verifies** `~/.config/seal/recovery-credentials` exists
3. **Prompts** for timelock duration (30m / 1h / 3h / 1d / 3d / 7d / custom)
4. **Encrypts** the file into `~/.config/seal/sealed-credentials` using `tle`
5. **Shreds and deletes** `~/.config/seal/recovery-credentials`
6. **Wipes** mike's bash history
7. **Locks** the system (generates dnsmasq whitelist + nftables DNS block)
8. **Prints** recovery instructions and reboot reminder

## Recovery Path

When timelock expires, recover with:

```bash
unseal
```

This writes `~/.config/seal/recovery-credentials` back with the decrypted content:

```
root_password=<password>
```

Use the root password to `su -`, then run allowlist commands directly:

```bash
su -
/opt/allowlist/allowlist.sh unlock
# Edit session domains with your preferred editor:
<editor> /opt/allowlist/allowlist.session.txt
/opt/allowlist/allowlist.sh lock
```

If you need to re-seal, you must be unlocked first:

```bash
/opt/allowlist/allowlist.sh unlock
/opt/allowlist/allowlist.sh seal
```

## Recovery Prerequisite: Network Connectivity

`unseal`, `seal`, and `sem` all require network access to reach the drand
beacon network (`api.drand.sh`, etc.). If WiFi is down, recovery fails:

```
WiFi → dnsmasq → drand API → tle download beacon → tle -d
```

After sudo removal (Phase 4), the only way to fix WiFi is through the
restricted sudo entries in `/etc/sudoers.d/99-mike-tools`:

| Command | Purpose |
|---------|---------|
| `sudo systemctl restart NetworkManager` | Recover from NM crash or config corruption |
| `sudo systemctl restart dnsmasq` | Recover DNS proxy (drand resolution fails without it) |
| `sudo systemctl restart nftables` | Recover firewall (ruleset reload — re-applies lockdown, no bypass) |
| `sudo ip link set wlp0s20f3 up` | Bring interface up after suspend/firmware crash |
| `sudo nft list ruleset` | Inspect firewall rules (read-only) |
| `sudo rfkill unblock wifi` | Unblock WiFi radio after soft-block |
| `sudo dmesg` | Check driver/firmware errors |

These entries **must be in place before** `sudo gpasswd -d mike sudo` is run;
they cannot be added afterwards.

### Lockdown Invariants

None of the restricted sudo entries can bypass the nftables DNS-leak firewall
or dnsmasq allowlist. The lockdown rests on three immutable files:

| File | Owner | Protection | Effect if restarted/modified |
|------|-------|------------|------------------------------|
| `/etc/nftables.conf` | root:root 644 | Read-only ruleset | `systemctl restart nftables` re-applies the lockdown — no bypass |
| `/etc/dnsmasq.conf` + `/etc/dnsmasq.d/` | root:root 644 | Read-only config | `systemctl restart dnsmasq` re-reads the same allowlist — no bypass |
| `/etc/resolv.conf` | root:root 644 + `chattr +i` | Immutable `127.0.0.1` | Cannot point upstream DNS elsewhere |

Every sudo entry is either read-only (`nft list`, `dmesg`) or reloads
existing root-owned config (`systemctl restart`). None allow modifying
the nftables ruleset, dnsmasq config, or resolv.conf.

## How Recovery Works

- drand API domains (`api.drand.sh`, `api2.drand.sh`, `api3.drand.sh`,
  `drand.cloudflare.com`) are whitelisted in the allowlist, so `tle`
  can reach the beacon network even when locked
- `/usr/local/bin/tle` is world-executable (755), so mike can run it directly
- `/usr/local/bin/unseal` wraps `tle -d` — fetches the beacon and decrypts `sealed-credentials` in one step
- No container, no `--dns` workaround, no image pull needed

## Removing Sudo

After verifying everything works, make the lockdown permanent:

```bash
sudo gpasswd -d mike sudo
```

After this:

| Action | Possible? |
|---|---|
| `sudo /opt/allowlist/allowlist.sh lock` | No — mike has no sudo |
| `/usr/local/bin/tle -d` (recovery) | Yes — world-executable, drand whitelisted |
| All user apps (browsers, terminals) | Yes — no escalation needed |
| `su -` (with root password) | Yes — PAM auth, not polkit |

## Files

| File | Purpose |
|---|---|
| `/opt/allowlist/allowlist.sh` | CLI with `seal` subcommand |
| `/opt/allowlist/generate-dnsmasq.sh` | Generates locked/unlocked dnsmasq config during lock |
| `~/.config/seal/recovery-credentials` | Plaintext recovery file (created by user, shredded by seal, recreated by unseal) |
| `~/.config/seal/sealed-credentials` | Timelock-encrypted credentials (mike:mike, 644) |
| `~/go/bin/tle` or `/usr/local/bin/tle` | Time-lock encryption binary |
