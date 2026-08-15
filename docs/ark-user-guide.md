# Ark — user guide

User-facing commands for the ark lockdown system. For policy and threat
model, see `RESTRICTION_POLICY.md`.

Mode model: `unrestricted` (everything open) ↔ `focused` (the daily working
state — distractions blocked, root password casked, sudo group removed). The
`locked` mode exists but has **no `ark lock` command** in the v1 surface.

---

## ark — mode transitions + rollback

Run via `sudo ark` (NOPASSWD — works even after the sudo group is removed).
On this machine `ark` is aliased to `sudo /usr/local/bin/ark`, so plain
`ark <cmd>` works in your shell.

### `ark enable`

Move from `unrestricted` → `focused`. Runs only from unrestricted mode
(enable-from-focused is an **error**, not a no-op). Confirmation flow:
mobile-cask offer → impact confirm → Timeshift snapshot → final point of no
return → cask root password → focused configs → sudo removal → reboot.

A 10 s countdown before the reboot can be cancelled; cancelling exits cleanly
with the abort gate still armed.

### `ark disable`

Move from `focused` → `unrestricted`. Runs only from focused mode:
- `unrestricted` → "Already unrestricted" (no-op)
- `locked` → **blocked** — exit via the lock timer or `ark abort`

**TLE-gated**: only runs after the timelock (cask) timer has expired —
fail-closed, the timer must be *proven* elapsed. Resets the root password to
the configured `DISABLE_ROOT_PASSWORD`, restores sudo, applies unrestricted
configs, reboots.

### `ark abort --now`

Roll back an **incomplete** enable (Timeshift snapshot restore). Gated by
`/opt/ark/state/enable.json` — the sole oracle:

- state file present → restore the pinned snapshot (rolls back root
  password, sudo, configs, mode)
- no state file → "Nothing to abort" (a clean enable deleted it)

`--now` skips the confirmation. TTY-bound — no scheduled/headless abort.

### `ark logs [cmd]`

Per-command evidence for the latest run of `enable`, `disable`, or `abort`:
`ark logs` lists the available files; `ark logs enable` prints the latest
`enable` run. Logs are evidence only — never read by the abort gate.

---

## cask — timelock credentials

Credentials are encrypted with a timelock (tle) at the time they're casked;
they can only be decrypted after the timer expires.

### `mcask`

`sudo mcask` — cask `mobile.credentials` (user-created) with timelock
encryption — encrypt, shred the plaintext, clear the clipboard and shell
history. Requires network stability, the tle binary, and a non-empty
`mobile.credentials`. (NOPASSWD via sudoers; the inline mobile-cask offer
inside `ark enable` uses the same reboot-free core.)

### `uncask`

`sudo uncask` — decrypt + display casked mobile credentials (NOPASSWD via
sudoers; reads the root-owned cask directory, decrypts, and shows the
plaintext). Prompts for confirmation before decrypting.

System credentials are never uncasked directly — recovery goes exclusively
through `ark disable` (root password reset + sudo restore + network
transition).

---

## netmgr — networking ergonomics

`inet` runs as your user; the `netmgr` commands below run via `sudo netmgr`
(NOPASSWD for the listed commands).

### `inet`

Run a command in the correct network context for the current mode:

- `inet <cmd>` — run the command in the internet namespace if the service is
  up and mode is `locked`; otherwise run it directly (with a warning)
- `inet --list` — show the grants table
- `inet --status` — show mode · namespace service · grants

### Generated shims + aliases

`sudo netmgr exec-grant deploy` (deploy-time; infra) builds thin shims in
`/usr/local/bin` (e.g. `inet "<binary>"`) and writes a generated alias block
into your bashrc (e.g. `podman-pull`). These are explicit, first-word-only
conveniences — a plain `podman pull` is never intercepted.

### `sudo netmgr namespace exec <cmd>`

Run an approved command with unrestricted internet from inside the namespace
(command allowlist enforced by netmgr, not sudoers granularity).

### Blocklist — editing (`unrestricted` only)

Blocklist mutations (`block`/`unblock`/`remove`/`add`, `exempt` edits,
`sources` edits) are gated to unrestricted mode. Edits go to the
version-controlled repo copies under `etc/ark/domains/focused/`, sync to the
live runtime files, then regenerate `blocklist.dnsmasq.conf`. All mutations
support `-y/--yes`, `--dry-run`, and write a `.bak` snapshot first.

- `sudo netmgr block <domain>` or `--group "<name>"` — block a domain or a
  whole group (uncomment + drop from exclude). Blocking a domain that is
  exactly exempted prompts to drop that exemption so the block actually
  applies; subdomain exemptions are kept (intended granularity).
- `sudo netmgr unblock <domain>` or `--group "<name>"` — allow a domain or a
  group (comment out + add to exclude). If the domain isn't in the custom
  list but is covered by a parent wildcard, it routes to an exemption
  instead.
- `sudo netmgr remove <domain>` or `--group "<name>"` — delete a domain or a
  whole group from the custom list + add to exclude.
- `sudo netmgr add <domain>... [--group "<name>"] [--file <path>]` — add
  domains to the custom list (prompts for the group banner; `--file` bulk
  imports).
- `sudo netmgr exempt add <domain>` / `exempt remove <domain>` /
  `exempt list` — manage `server=/domain/#` overrides that unblock specific
  subdomains from a parent wildcard (e.g. block `youtube.com`, allow
  `studio.youtube.com`).
- `sudo netmgr sources list` / `add <url>` / `toggle <id>` / `update` —
  manage upstream sources in `sources.json` (`update` re-downloads all
  enabled sources + regenerates).

### `sudo netmgr lookup <domain>`

Show blocked/exempt status for one domain (exemption wins) — works in any
mode:

```
$ sudo netmgr lookup facebook.com
BLOCKED  blocklist-custom.txt (your list)

$ sudo netmgr lookup studio.youtube.com
EXEMPT  blocklist-exceptions.txt (unblocked from youtube.com wildcard)

$ sudo netmgr lookup example.com
NOT FOUND
```

### `sudo netmgr search <pattern>`

Regex search across all blocklists (root-owned files; the NOPASSWD rule keeps
this usable in focused/locked).

### `sudo netmgr allowlist`

- `sudo netmgr allowlist list [--section <infra|base|session>]` — list
  allowlist domains (works in any mode)
- `sudo netmgr allowlist search <pattern>` — search allowlist domains

### `sudo netmgr exec-grant`

Manage which binaries the namespace may run (edits are gated to
`unrestricted` mode):

- `sudo netmgr exec-grant add <binary> [arg]` — grant a binary
- `sudo netmgr exec-grant remove <binary> [arg]` — revoke a binary
- `sudo netmgr exec-grant list` — show the grants table

### `sudo netmgr status`

Show current mode, dnsmasq endpoint, and allowlist + blocklist counts
(custom / upstream / exemptions / generated).

---

## Command matrix

| From | Command | Result |
|------|---------|--------|
| unrestricted | `ark enable` | → focused (snapshot + cask + sudo removal + reboot) |
| unrestricted | `ark disable` | No-op: "Already unrestricted" |
| focused | `ark enable` | Error (run `ark disable` first) |
| focused | `ark disable` | → unrestricted (TLE-gated; root pw reset + reboot) |
| locked | `ark disable` | Blocked — lock timer expiry or `ark abort --now` |
| any (gate armed) | `ark abort --now` | Timeshift restore of the pinned snapshot |
| any | `ark logs [cmd]` | Evidence from the latest run |
