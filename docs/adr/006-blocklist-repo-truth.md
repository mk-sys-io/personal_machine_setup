# Repo blocklists are truth; netmgr syncs to root-owned live

- Status: Accepted (2026-09-28) · Source:
  `plans/completed/netmgr-improvements.md`

## Decision

Repo files (`sources.json`, `blocklist-custom.txt`,
`blocklist-exclude.txt`, `blocklist-exceptions.txt`) are the source
of truth. `netmgr` edits the repo copy, then `_sync_to_live()`
copies to the root-owned live location; the user commits for
portability. The three files have non-overlapping roles: custom holds
parent wildcards, exceptions unblock subdomains
(`server=/domain/#`), exclude drops domains from generation.
Enforcement is sudo removal — no `chattr`.

## Why

Root ownership plus mode gating (`require_unrestricted()`) makes live
copies unmodifiable in focused/locked mode without immutable flags.
Exclude ≠ exceptions is documented because the confusion re-litigates:
only `server=/domain/#` overrides a parent wildcard.
`Rejected: flat root-level layout — no versioning; delete-on-empty —
git drops empty files, so always-header writes; runtime auto-recreate
of repo files — seed-only deploy; dnsmasq input format — hosts/ABP
cover sources; single toggle — block/unblock/remove split; chattr +i
— sudo removal suffices.`
`Revisit only if: none named (AI domain-family detection is noted as
out-of-scope future, not a trigger).`

## Consequences

- Good: user commits carry full blocklist state to a fresh machine;
  header-only files keep git tracking a valid empty state.
- Bad (accepted cost): every mutation is edit-plus-sync, and
  repo/live drift if the user forgets to commit.
