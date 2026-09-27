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
| Accepted (2026-09-27) | Split backup/restore modules; trunk only restores | [003-backup-restore-split-modules.md](003-backup-restore-split-modules.md) |
| Accepted (2026-09-27) | GPG key survives via restic-encrypted export in B2 | [002-gpg-key-survives-via-restic.md](002-gpg-key-survives-via-restic.md) |
| Accepted (2026-09-27) | Stay on restic; S3 API, accept stale-lock ops cost | [001-restic-over-alternatives.md](001-restic-over-alternatives.md) |

## Filename rule

`NNN-short-slug.md`. No `ADR-` prefix (redundant under this folder).
Numbers never reused. Slug states the verdict, short —
e.g. `001-restic-over-alternatives.md`.

## Skeleton (120–200 words, hard cap 250)

`# <verdict>` / `- Status: … · Supersedes: … · Source: …` /
`## Decision` (what, 2–4 sentences, active voice) /
`## Why` (triggering constraint first; `Rejected: <alt> — <one line>`;
`Revisit only if: <trigger>`) /
`## Consequences` (`Good:` / `Bad (accepted cost):`).

## Lifecycle

Accepted records are append-only. Change = new file + flip old
Status in the same commit, never rewrite history.
`plans/decisions/` is the drafting inbox; promotion = copy into
`docs/adr/` on acceptance.
