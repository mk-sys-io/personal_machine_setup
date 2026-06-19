# Seal — Credential Encryption & Recovery

## Directory Layout

All seal-related files live in `~/.config/seal/`:

```
~/.config/seal/
├── recovery-credentials     # Plaintext (created by user, shredded by seal, recreated by unseal)
├── sealed-credentials       # Encrypted blob (created by seal)
└── seal.log                 # Timestamped log of seal/unseal operations
```

## Commands

### `seal` — Encrypt credentials and lock the system

Run via `/opt/allowlist/allowlist.sh seal` (as root via sudo). The seal command:

1. **Init** — `mkdir -p ~/.config/seal` then truncates `seal.log`
2. **Log** — Writes `[timestamp] seal: started`
3. **Encrypt** — Copies plaintext `recovery-credentials` to temp, encrypts with `tle -e`, shreds temp
4. **Shred** — Permanently deletes `recovery-credentials` via `shred -u`
5. **Lock** — Activates dnsmasq whitelist + nftables DNS-leak block
6. **Reboot** — 6-second countdown then `reboot`

On encryption failure: logs `[timestamp] seal: ENCRYPTION FAILED` + exits.
On success: logs `[timestamp] seal: encryption OK` then proceeds to lock.

Every `seal` run **truncates** the log — each seal cycle starts fresh.

### `unseal` — Decrypt sealed credentials

Run via `/usr/local/bin/unseal` (world-executable, no sudo needed). The unseal command:

1. **Check** — Verifies `~/.config/seal/sealed-credentials` exists
2. **Log** — Appends `[timestamp] unseal: decrypting...` to `seal.log`
3. **Decrypt** — Runs `tle -d` with stderr appended to `seal.log`
4. **Result** — On success: logs `SUCCESS`, opens `recovery-credentials` in `nano`
                On failure: logs `FAILED`, prints error path

`unseal` **appends** to `seal.log` — previous log entries survive until the next seal truncates them.

## What's NOT Logged

- `lock` and `unlock` subcommands — no seal.log entries
- System reboot — handled by systemd journal

## File Reference

| File | Owner | Perms | Protection | Purpose |
|------|-------|-------|------------|---------|
| `~/.config/seal/recovery-credentials` | mike:mike | 600 | — | Plaintext root password (user creates, seal shreds, unseal recreates) |
| `~/.config/seal/sealed-credentials` | mike:mike | 644 | `chattr +i` immutable | Timelock-encrypted credentials (created by seal, read by unseal); immutable flag prevents accidental deletion |
| `~/.config/seal/seal.log` | mike:mike | 644 | — | Operation log (truncated by seal, appended by unseal) |

## Security Model

- `seal` runs as root (via `sudo /opt/allowlist/allowlist.sh seal`) — needs root to lock the system
- `unseal` is world-executable (`/usr/local/bin/unseal`, root:root, 755) — runs as user mike, no escalation needed
- The seal directory and all contents are `chown`'d to `mike:mike` after seal creation so unseal can write the log
- Recovery works when locked because drand API domains are allowlisted and `tle -d` only needs outbound HTTPS to the drand beacon network
