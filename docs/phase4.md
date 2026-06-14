# Phase 4 — Seal & Sudo Removal

## Overview

Phase 4 makes the allowlist lockdown irreversible from user `mike`'s
perspective. Recovery is possible only via a podman container after
a timelock expires.

## The `seal` Command

```bash
sudo /opt/allowlist/allowlist.sh seal
```

Reads `~/.config/recovery-credentials` (must exist — user creates it beforehand):

1. **Checks** `tle` is installed (`~/go/bin/tle` or `/usr/local/bin/tle`)
2. **Verifies** `~/.config/recovery-credentials` exists
3. **Prompts** for timelock duration (30m / 1h / 3h / 1d / 3d / 7d / custom)
4. **Encrypts** the file into `~/.config/sealed-credentials` using `tle`
5. **Shreds and deletes** `~/.config/recovery-credentials`
6. **Wipes** mike's bash history
7. **Locks** the system (generates dnsmasq whitelist + nftables DNS block)
8. **Prints** recovery instructions and reboot reminder

## Recovery Path (after sudo removal)

When timelock expires, recover with:

```bash
podman run --rm \
  -v ~/.config:/host-config:rw \
  alpine sh -c "
    apk add -q curl tar
    curl -fsSL -o /tmp/tlock.tar.gz \
      https://github.com/drand/tlock/releases/download/v1.2.0/tlock_1.2.0_linux_amd64.tar.gz
    tar xzf /tmp/tlock.tar.gz -C /usr/bin tle
    /usr/bin/tle -d -o /host-config/recovery-credentials /host-config/sealed-credentials
  "
```

This writes `~/.config/recovery-credentials` back with the decrypted content:

```
root_password=<password>
```

Edit the file, re-run `seal` to lock again for the next focus cycle.
Use the root password to `su -`, then run allowlist commands directly:

```bash
su -
/opt/allowlist/allowlist.sh unlock
/opt/allowlist/allowlist.sh add newdomain.com
/opt/allowlist/allowlist.sh lock
```

## How Recovery Works

- Podman rootless runs in its own network namespace (bypasses host nftables)
- The container downloads and extracts `tle` from the GitHub release tarball
- `tle` decrypts `sealed-credentials` and writes to `recovery-credentials` on the host
- No sudo needed — `podman run` is user-level
- The cycle repeats: edit `recovery-credentials`, re-run `seal` → encrypt → delete → lock

## Removing Sudo

After verifying everything works, make the lockdown permanent:

```bash
sudo gpasswd -d mike sudo
```

After this:

| Action | Possible? |
|---|---|
| `sudo /opt/allowlist/allowlist.sh lock` | No — mike has no sudo |
| `podman run ...` (recovery) | Yes — rootless podman |
| All user apps (browsers, terminals) | Yes — no escalation needed |
| `su -` (with root password) | Yes — PAM auth, not polkit |

## Files

| File | Purpose |
|---|---|
| `/opt/allowlist/allowlist.sh` | CLI with `seal` subcommand |
| `/opt/allowlist/generate-dnsmasq.sh` | Generates locked/unlocked dnsmasq config during lock |
| `~/.config/recovery-credentials` | Plaintext recovery file (created by user, deleted by seal, recreated by recovery) |
| `~/.config/sealed-credentials` | Timelock-encrypted credentials (mike:mike, 644) |
| `~/go/bin/tle` or `/usr/local/bin/tle` | Time-lock encryption binary |
