# Architecture Decisions

## [001] Replace podman recovery with allowlisted drand API

**Date**: 2026-06-15

**Status**: Accepted

**Context**: The original recovery method used `podman run alpine` to download `tle` inside a container and decrypt sealed credentials, bypassing the host's locked DNS via `--dns 1.1.1.1`.

**Problem**: Container launch was unreliable in practice:
- Failed on certain kernel versions
- Alpine image pull added latency and a network dependency
- The `--dns 1.1.1.1` workaround was fragile
- Rarely-tested recovery path with high complexity

**Decision**: Add the four drand API endpoints to the allowlist and decrypt directly on the host:
- `api.drand.sh`
- `api2.drand.sh`
- `api3.drand.sh`
- `drand.cloudflare.com`

**Consequences**:
- Positive: Eliminates podman from the recovery path entirely
- Positive: Recovery is a single `tle -d` command, no container
- Positive: Works when locked — drand domains are always whitelisted
- Negative: drand endpoints are reachable from the locked state (acceptable — they only serve randomness beacons, no general internet access)

## [002] Seal reorganization — dedicated directory, unseal wrapper, structured logging

**Date**: 2026-06-16

**Status**: Accepted

**Context**: Seal files were scattered across `~/.config/` with a stale `.meta`
sidecar file, no operation logging, and recovery required memorizing `tle -d` flags.

**Decision**:
- Consolidate all seal files into `~/.config/seal/`
- Remove the `.meta` sidecar (redundant — info is embedded in the seal cycle)
- Add timestamped logging to `seal.log` for both seal and unseal
- Create `/usr/local/bin/unseal` — world-executable `tle -d` wrapper

**Consequences**:
- Positive: Single directory for all seal state
- Positive: No stale metadata to drift from reality
- Positive: Recovery is `unseal` — no flags needed
- Positive: Operations are auditable via seal.log
- Positive: unseal is world-executable (755), works without sudo
- Negative: seal runs as root (via sudo) → must chown seal dir to mike:mike
  so unseal (user) can write the log

## [003] NetworkManager polkit carve-out for post-lockdown WiFi connectivity

**Date**: 2026-06-18

**Status**: Accepted

**Context**: The `99-internet-lockdown.rules` policy blanket-denies all
polkit actions for user mike. This blocks `org.freedesktop.NetworkManager.
settings.modify.system` (adding/editing system WiFi connections), which
requires `auth_admin_keep` and consults polkit. All other NM actions
(scan, connect, toggle WiFi) have `allow_active: yes` and bypass polkit
entirely.

Post-lockdown, sudo will be revoked. The user must still be able to:
- Add new WiFi connections via `nmtui`/`nmcli`
- Modify existing connections
- This is critical for the `unseal`/decrypt path (drand API requires
  internet connectivity)

**Decision**: Add a guard in the lockdown rule that returns `undefined`
(no opinion) for all `org.freedesktop.NetworkManager.*` actions, causing
them to fall through to Debian's default NM rule
(`/usr/share/polkit-1/rules.d/org.freedesktop.NetworkManager.rules`).
That rule grants `settings.modify.system` to members of the `netdev`
group without authentication. User mike is already in the `netdev` group.

**Scope of the carve-out**:
- Only `org.freedesktop.NetworkManager.*` actions are exempted
- `pkexec` remains blocked (core lockdown purpose)
- `systemctl` actions remain blocked
- All other polkit actions remain blocked

**Why this is safe**:
- The user must already be logged into an active local session to use
  `nmtui` — the carve-out doesn't enable remote privilege escalation
- The NM service itself runs as root and independently enforces its
  own security (wpa_supplicant credentials, keyring access)
- Granting NM control to the `netdev` group is Debian's default behavior
  — this just restores it after the lockdown overrode it
- No general internet restriction or DNS lockdown is affected

**Consequences**:
- Positive: `nmtui`/`nmcli` work for WiFi management without sudo
- Positive: Post-lockdown recovery path for network connectivity
- Positive: No new privileges granted — `netdev` group was already
  the Debian standard mechanism
- Negative: Members of the `netdev` group (currently only mike) can
  modify system network connections without authentication (acceptable —
  this is Debian's default)

## [004] seal_lib.py — dual deployment for shared library across root/user domains

**Date**: 2026-06-23

**Status**: Accepted

**Context**: The seal/unseal system was refactored to extract shared code
(logging, gates, clipboard, encryption) into a single `seal_lib.py`. Two
callers import it:

- `seal.py` — deployed to `/opt/allowlist/` (root:root, 750)
- `unseal.py` — deployed to `/usr/local/bin/unseal` (world-executable,
  runs as user mike with no sudo)

Three constraints collide:
1. **Ruff static analysis** — `sys.path.insert()` hacks at module level
   produce `E402` (import not at top) and unresolved import errors. The
   only way Ruff can resolve `import seal_lib as lib` without
   configuration is if both files are peers in the same directory.
2. **Root vs user ownership** — `/opt/allowlist/` is root-owned. Unseal
   runs as user mike with no sudo. Placing `unseal.py` in
   `/opt/allowlist/` would block execution.
3. **Single source of truth** — `seal_lib.py` must not be duplicated
   with drift. Every caller must import the same canonical file.

**Decision**:

*In the repository* (development) — a symlink gives Ruff a peer import
while keeping one canonical file:

```
.config/
├── allowlist/scripts/
│   ├── seal.py              import seal_lib as lib
│   ├── seal_lib.py          ← canonical source
└── scripts/
    ├── seal_lib.py          → symlink to ../allowlist/scripts/seal_lib.py
    └── unseal.py            import seal_lib as lib (peer via symlink)
```

The symlink is a development-only artifact for Ruff. It is never deployed.
`git` tracks symlinks natively — `git clone` recreates it automatically.
`install.sh` verifies the symlink's existence and target integrity.

*On deploy* (install.sh) — the canonical file is copied to both domains
so each caller has a peer import at runtime:

| Deployed path | Owner | Mode | Source |
|---|---|---|---|
| `/opt/allowlist/seal.py` | root:root | 750 | `.config/allowlist/scripts/seal.py` |
| `/opt/allowlist/seal_lib.py` | root:root | 750 | `.config/allowlist/scripts/seal_lib.py` |
| `/usr/local/bin/seal_lib.py` | root:root | 644 | `.config/allowlist/scripts/seal_lib.py` |
| `/usr/local/bin/unseal` | mike:mike | 755 | `.config/scripts/unseal.py` |

All imports are `import seal_lib as lib` — peer import, no
`sys.path.insert`, no conditionals, no `__all__`.

**Alternatives considered and rejected**:

1. **`sys.path.insert` at module level** — Triggers Ruff `E402` and
   unresolved import errors. Fragile: ordering-dependent, hard to debug
   when a different `seal_lib.py` shadows the expected one.
2. **`__all__` + `from seal_lib import *`** — Requires manual maintenance
   of the export list. Ruff still cannot resolve the re-exported names.
3. **Bash wrapper at `/usr/local/bin/unseal` wrapping
   `/opt/allowlist/unseal.py`** — Places the entry point in a root-only
   directory; user would need sudo to run it, defeating the purpose.

**Why `chown mike:mike` on unseal**:
The sudo policy (`99-mike-tools`) does not grant passwordless execution
of `/opt/allowlist/` scripts. Unseal must be owned by mike (mode 755)
so the user can invoke it directly without sudo. The library file
`seal_lib.py` remains root-owned (mode 644) — it is read, not executed.

**Consequences**:
- Positive: Ruff resolves all imports cleanly — no configuration needed
- Positive: No runtime path hacks in production — both callers use
  `import seal_lib as lib`
- Positive: Single canonical source — edit one file, both callers pick
  up the change
- Positive: Unseal runs as user — no sudo required, works post-lockdown
- Negative: `install.sh` copies the same file to two locations — the two
  copy lines for `seal_lib.py` (`/opt/allowlist/` and `/usr/local/bin/`)
  must be kept in sync
- Note: Anyone adding a new function to `seal_lib.py` just adds it there
  and calls it via `lib.` prefix in either caller — no additional
  plumbing needed
