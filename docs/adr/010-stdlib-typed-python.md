# Ark Python is stdlib-only, fully typed, no pip

- Status: Accepted (2026-09-28) · Source:
  `plans/reference/ark/design-notes.md` (Implementation Language)

## Decision

All Ark Python is type-hinted, stdlib-only, with no frameworks and no
pip dependencies. The tool must work on a fresh system by copying a
`.py` file to `/usr/local/bin`. Output is raw ANSI (no Rich/colorama);
shared logging is a stdlib wrapper. Shell scripts stay bash.

## Why

Fresh-machine and lockdown deployability: zero install step beyond
copy, zero supply chain beyond the stdlib. Types cost nothing at
runtime and carry the architecture's clarity. Scoped to the language
section only — commitment tiers, seal invariant, dry-run, and state
machine are separate future ADRs, not claimed here.
`Rejected: third-party frameworks — unneeded surface; pip
dependencies — break copy-deploy; Rich/colorama — raw ANSI suffices;
non-stdlib logging — stdlib wrapper instead; bash-to-Python rewrites
for generate/verify scripts — stay bash.`
`Revisit only if: none named.`

## Consequences

- Good: copy-deploy keeps every Ark tool working offline and under
  lockdown; typed stdlib ages well.
- Bad (accepted cost): hand-rolled patterns where a library would be
  terser; contributors write more verbose code.
