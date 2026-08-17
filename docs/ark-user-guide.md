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

After the reboot, run `sudo ark status` to confirm the transition landed
(focused mode, sudo removed, DNS drop rules active).

### `ark disable`

Move from `focused` → `unrestricted`. Runs only from focused mode:
- `unrestricted` → "Already unrestricted" (no-op)
- `locked` → **blocked** — exit via the lock timer or `ark abort`

**TLE-gated**: only runs after the timelock (cask) timer has expired —
fail-closed, the timer must be *proven* elapsed. Resets the root password to
the configured `DISABLE_ROOT_PASSWORD`, restores sudo, applies unrestricted
configs, reboots.

After the reboot, run `sudo ark status` to confirm sudo is restored and the
machine is back in `unrestricted`.

### `ark abort`

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

### `ark status`

Live-state report of the whole system — **not** a log replay. It reads the
current mode and runs live probes to answer "is the lockdown actually
enforced right now?":

- **Mode and access** — mode state, sudo-group membership (absent in
  `focused`/`locked`, present in `unrestricted`), abort-gate state
- **Network enforcement** — dnsmasq allowlist endpoint, direct-DNS drop
  rules, DNS resolution as your user, `resolv.conf` → dnsmasq, root DNS
- **Internet namespace** — namespace service, netns/veth/routing, granted
  apps (the `inet` allowlist)
- **Timelock** — cask metadata and the remaining lock time
- **Browser policies** — Brave / Chromium / Chrome / Firefox policy files

Flags: `-v` adds per-check detail (rule counts, raw cask metadata);
`--plain` disables ANSI color; `--json` emits a structured report
(`sections`, `summary`, tri-state `ok`). Exit code: `0` when every enforced
check passes, `1` when any fails — informational rows never affect it.

Ritual: run `sudo ark status` after each enable/disable reboot to confirm
the transition landed, and any time you're unsure what state the machine
is in.

> `ark status` is the whole-system summary. `netmgr status` is the
> netmgr-only view (mode + dnsmasq endpoint + allowlist/blocklist counts) —
> separate entry points, no conflict.

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

`inet` runs as your user. `netmgr` is aliased to `sudo /usr/local/bin/netmgr`
(the same pattern as the `ark` alias), so the admin commands below work as
`netmgr <cmd>`; the longhand `sudo /usr/local/bin/netmgr <cmd>` form still
works. All listed commands are NOPASSWD via sudoers.

### The internet namespace — the sanctioned bypass

Focused/locked modes restrict the *host* at the DNS layer: dnsmasq serves
only allowlisted domains and direct DNS from user processes is dropped by
the firewall. The internet namespace (`internet-netns`) is the sanctioned
exception — an isolated network stack with real DNS and full egress, so
approved tools (e.g. `opencode`) keep working during a focus session.

- The command allowlist (`netns-exec-allowlist.txt`, root-owned) is the gate:
  only granted binaries run inside the namespace — everything else is refused.
- Grant edits (`exec-grant add/remove`) are gated to `unrestricted` mode, so
  from focused/locked the allowlist is frozen: netmgr cannot be widened into
  a general internet bypass.
- Approved grants keep full egress by design — the gate controls *which*
  tools get the namespace, not *what* those tools do.
- For the full threat model and design constraints, see
  `RESTRICTION_POLICY.md`.

### `inet` — run a command in the right network context

`inet` picks the correct network context automatically. `inet --help` lists
the options; the behavior it does not explain is below.

- `inet` (bare) or `inet --list` — show the grants table
- `inet --status` — show mode · namespace service · grants:
- `inet --ark-logs` — show path to the netmgr log file (`~/.local/logs/netmgr.log`)

  ```
  Mode: focused
  Service: internet-netns (running)
  Grants: 1 (opencode)
  ```

- `inet <cmd>` — run the command in the right context:

  | Namespace service | Mode | Behavior |
  |-------------------|------|----------|
  | running | any | run inside the namespace via `netmgr namespace exec` (allowlist-gated) |
  | stopped | unrestricted / focused | run directly, with a stderr warning |
  | stopped | locked | prompt y/N to continue without the namespace; non-interactive runs abort |

### Namespace service

- `systemctl status internet-netns` — check the service
- `sudo systemctl start internet-netns` / `stop` — bring it up or down
- `netmgr namespace status` — JSON: namespace · veth · routing

### Grants — thin only

The allowlist supports **bare binary paths only** — a granted binary runs with
any of its subcommands (the `"$@"` passes through). Adding an approved binary
= one-line edit to the source file (`etc/ark/netns-exec-allowlist.txt`).

### `netmgr namespace exec <cmd>`

The low-level primitive behind `inet`: run an allowlisted command with
unrestricted internet inside the namespace. It runs as your user (drops to
`SUDO_USER`), and the command allowlist is enforced by netmgr, not sudoers
granularity. Prefer `inet <cmd>` — it adds the mode-aware fallback.

### Blocklist — editing (`unrestricted` only)

Blocklist mutations (`block`/`unblock`/`remove`/`add`, `exempt` edits,
`sources` edits) are gated to unrestricted mode. Edits go to the
version-controlled repo copies under `etc/ark/domains/focused/`, sync to the
live runtime files, then regenerate `blocklist.dnsmasq.conf`. All mutations
support `-y/--yes`, `--dry-run`, and write a `.bak` snapshot first.

- `netmgr block <domain>` or `--group "<name>"` — block a domain or a
  whole group (uncomment + drop from exclude). Blocking a domain that is
  exactly exempted prompts to drop that exemption so the block actually
  applies; subdomain exemptions are kept (intended granularity).
- `netmgr unblock <domain>` or `--group "<name>"` — allow a domain or a
  group (comment out + add to exclude). If the domain isn't in the custom
  list but is covered by a parent wildcard, it routes to an exemption
  instead.
- `netmgr remove <domain>` or `--group "<name>"` — delete a domain or a
  whole group from the custom list + add to exclude.
- `netmgr add <domain>... [--group "<name>"] [--file <path>]` — add
  domains to the custom list (prompts for the group banner; `--file` bulk
  imports).
- `netmgr exempt add <domain>` / `exempt remove <domain>` /
  `exempt list` — manage `server=/domain/#` overrides that unblock specific
  subdomains from a parent wildcard (e.g. block `youtube.com`, allow
  `studio.youtube.com`).
- `netmgr sources list` / `add <url>` / `toggle <id>` / `update` —
  manage upstream sources in `sources.json` (`update` re-downloads all
  enabled sources + regenerates).

### `netmgr lookup <domain>`

Show blocked/exempt status for one domain (exemption wins) — works in any
mode:

```
$ netmgr lookup facebook.com
BLOCKED  blocklist-custom.txt (your list)

$ netmgr lookup studio.youtube.com
EXEMPT  blocklist-exceptions.txt (unblocked from youtube.com wildcard)

$ netmgr lookup example.com
NOT FOUND
```

### `netmgr search <pattern>`

Regex search across all blocklists (root-owned files; the NOPASSWD rule keeps
this usable in focused/locked).

### `netmgr allowlist`

- `netmgr allowlist list [--section <infra|base|session>]` — list
  allowlist domains (works in any mode)
- `netmgr allowlist search <pattern>` — search allowlist domains

### `netmgr exec-grant add|remove|deploy`

Manage the namespace allowlist (add/remove are `unrestricted`-only):

- `netmgr exec-grant add <binary> [arg]` — grant a binary. Resolves the real
  path, then prints the line to sync into `etc/ark/netns-exec-allowlist.txt`
  (runtime edits revert on the next redeploy unless synced) and warns if
  something earlier on your PATH would shadow the new shim.
- `netmgr exec-grant remove <binary> [arg]` — revoke a grant.
- `netmgr exec-grant list` — show the grants table.
- `netmgr exec-grant deploy [--bashrc <path>]` — regenerate the thin shims,
  `inet`, and the generated alias block (root-only). Runs automatically at
  deploy and on mode transitions; only needed manually after hand-editing
  the allowlist seed.

### `netmgr status`

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
| locked | `ark disable` | Blocked — lock timer expiry or `ark abort` |
| any (gate armed) | `ark abort` | Timeshift restore of the pinned snapshot |
| any | `ark logs [cmd]` | Evidence from the latest run |
| any | `ark status [-v] [--plain\|--json]` | Live system report (mode, sudo, network, namespace, timelock; exit 1 on issues) |
