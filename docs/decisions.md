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
