# gomplate renders Ark path only, strict vars

- Status: Accepted (2026-09-28) · Source: `plans/archive/gomplate-migration.md`

## Decision

Ark lockdown sources render `{{ .Env.VAR }}` via gomplate, scoped by
`ARK_RENDER_PATHS` and self-discovered with `grep -rlZ`, replacing
the 40-line sed `@PLACEHOLDER@` substitution in `lib/60-ark.sh`. The
`Makefile` user path is excluded: dotfiles deploy by verbatim `cp`
plus one sed line for the single live substitution.

## Why

Strict `{{ .Env.VAR }}` fails fast on config errors at install time;
`getenv` variants forgive and hide them. Self-discovery means new
files and variables need no renderer changes — only
`config.env.template` plus source edits.
`Rejected: 40-line sed — hand-rolled, per-file churn; forgiving
getenv — hides missing vars; gomplate-for-dotfiles — a GitHub-API
binary dependency for 1 substitution breaks zero-dep fresh-clone use.`
`Revisit only if: dotfiles grow to ≥3 live vars in ≥2 files — then
opt-in make dotfiles-render, never default.`

## Consequences

- Good: 18 files / ~80 placeholders render uniformly; adding a deploy
  dir is one config line; consistency means right tool per job, not
  same tool everywhere.
- Bad (accepted cost): gomplate rides the GitHub-API install path
  (`packages/github_binary.txt`); root deploy needs `set -a` before
  sourcing `config.env`.
