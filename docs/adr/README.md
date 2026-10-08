# Decision records

Portable "why" log. `AGENTS.md` + committed `docs/` are the portable
source of truth; `plans/` (gitignored) is machine-local — anything
that must survive a fresh machine lives here, not there.

## Worthiness gate

File an ADR iff ≥2 boxes check, or any ⭐ alone. Else: commit message
(trivial) or nothing (ticks, refreshes, scaffolding).

- [ ] Irreversible or expensive to reverse ⭐
- [ ] Security/custody boundary crossed ⭐
- [ ] ≥2 serious alternatives rejected
- [ ] Cross-module impact (≥2 of dotfiles/dev/lib/ark/etc)
- [ ] High re-litigation risk
- [ ] Revisit trigger is nameable

## Index

| Status | Title | File |
|---|---|---|
| Accepted (2026-10-08) | Restore runs standalone post-install.py; trunk converges configs only | [012-restore-standalone-post-install.md](012-restore-standalone-post-install.md) |
| Accepted (2026-09-28) | User-scoped entry points refuse root; privilege only via internal sudo | [011-refuse-root-precondition.md](011-refuse-root-precondition.md) |
| Accepted (2026-09-28) | Ark Python is stdlib-only, fully typed, no pip | [010-stdlib-typed-python.md](010-stdlib-typed-python.md) |
| Accepted (2026-09-28) | Test every change in a disposable full-replica VM | [008-staging-vm-golden.md](008-staging-vm-golden.md) |
| Accepted (2026-09-28) | VPN defense is friction, not a wall | [007-vpn-friction-defense.md](007-vpn-friction-defense.md) |
| Accepted (2026-09-28) | Repo blocklists are truth; netmgr syncs to root-owned live | [006-blocklist-repo-truth.md](006-blocklist-repo-truth.md) |
| Accepted (2026-09-28) | gopass is the sole API-key store; registry splits from Pi | [005-gopass-sole-store.md](005-gopass-sole-store.md) |
| Accepted (2026-09-28) | gomplate renders Ark path only, strict vars | [009-gomplate-ark-core-only.md](009-gomplate-ark-core-only.md) |
| Accepted (2026-09-28) | Stay on Makefile; harden clean-stale, defer managers | [004-makefile-stays.md](004-makefile-stays.md) |
| Superseded (2026-10-08) by 012 (trunk wiring; split stands) | Split backup/restore modules; trunk only restores | [003-backup-restore-split-modules.md](003-backup-restore-split-modules.md) |
| Accepted (2026-09-27) | GPG key survives via restic-encrypted export in B2 | [002-gpg-key-survives-via-restic.md](002-gpg-key-survives-via-restic.md) |
| Accepted (2026-09-27) | Stay on restic; S3 API, accept stale-lock ops cost | [001-restic-over-alternatives.md](001-restic-over-alternatives.md) |

## Filename rule

`NNN-short-slug.md`. No `ADR-` prefix (redundant under this folder).
Numbers never reused. Slug states the verdict, short —
e.g. `001-restic-over-alternatives.md`.

## Skeleton (120–200 words, hard cap 250 at acceptance)

`# <verdict>` / `- Status: … · Supersedes: … · Source: …` /
`## Decision` (what, 2–4 sentences, active voice) /
`## Why` (triggering constraint first; `Rejected: <alt> — <one line>`;
`Revisit only if: <trigger>`) /
`## Consequences` (`Good:` / `Bad (accepted cost):`).

## Lifecycle

Proposed records revise freely; accepted bodies never rewrite.
Status line is the only in-place edit (plus typo fixes).

- Default: dated `## Amendment (YYYY-MM-DD)` appendix. Verdict,
  rationale, costs must stay true. Budget: max 2, +150 words.
- New file iff the verdict line would change or a cost flips. New
  files face the gate; amendments ride the parent's number. Same
  commit flips old Status, links both ways.
- Second file on one theme → group in `docs/adr/<theme>/` (keep
  `NNN-` names); fix index + inbound links same commit. Flat else.
- `plans/decisions/` is the drafting inbox; promotion = copy in.
