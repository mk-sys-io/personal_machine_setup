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
