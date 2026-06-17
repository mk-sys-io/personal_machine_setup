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
