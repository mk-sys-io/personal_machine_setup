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
