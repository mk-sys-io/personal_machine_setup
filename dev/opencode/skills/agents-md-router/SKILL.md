---
name: agents-md-router
description: Authors and reviews repo-local AGENTS.md orientation routers. Use when writing, fixing, onboarding via, or judging a proposed change to any repo-root AGENTS.md router. Does not cover global user-level files.
license: MIT
compatibility: opencode, claude-code
metadata:
  version: "1.0.0"
  domain: docs
  role: guide
---

# Repo-Local AGENTS.md Routers

Makes you expert at repo-root orientation files: create, fix, and judge
proposed changes. Two modes: **author** (write/fix) and **review** (verdict
on a suggested change). Global user-level files are a different skill —
never apply this one's patterns there.

## Author mode

1. Open with a 1–2 sentence WHAT: what the repo is + live/mirror status
   (where edits take effect).
2. Component table (`| Component → start |`): biggest parts only. A row must
   earn its place — include it only if omitting it would cause a wrong
   action AND the agent can't infer it. Cells hold path + one-verb purpose
   + deploy destination. Names, values, flags, per-provider detail live
   behind pointers, never in the table.
3. Session-start: git commands (`git status --short`, `git log --oneline -5`,
   `git branch --show-current`). Never memory-system prose.
4. Verify tiers: error → fix now; warning → fix or note trade-off; info/hint
   → tolerate. Name the linters, not their configs.
5. Read `references/agents-md-cookbook.md` before writing — ranked content,
   must-NOT list, budgets, and cut-tests live there.

## Review mode

Judge a proposed change SOUND or UNSOUND, citing the cookbook rule:

- SOUND: keeps router shape, routes instead of dumping, budget holds,
  every line passes "would-removal-cause-mistake?".
- UNSOUND: prose dump, specifics in the table, file trees, linter's-job
  prose, stale paths, ALL-CAPS forcing, secrets.
- One-line verdict for trivially sound changes; full reasoning only when
  rejecting.
