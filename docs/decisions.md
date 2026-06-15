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
