---
name: global-agents-md
description: Authors and reviews global user-level agent instruction files. Use when writing, fixing, or judging a proposed change to a global AGENTS.md, CLAUDE.md, or equivalent user-level file that loads in every project. Does not cover repo-local routers.
license: MIT
compatibility: opencode, claude-code
metadata:
  version: "1.0.0"
  domain: docs
  role: guide
---

# Global Agent Instruction Files

Makes you expert at user-level files that stack on **every** project:
create, fix, and judge proposed changes. Two modes: **author** (write/fix)
and **review** (verdict on a suggested change). Repo-local routers are a
different skill — never apply this one's patterns there.

## Author mode

1. Universal-only content: tone, hard rules, machine notes — things true in
   all repos. Anything per-repo is pollution: demote it to the project file.
2. Promotion rule: the same rule appearing in 3 project files earns a move
   to global. Nothing is born global.
3. Per-tool global paths differ and churn (`~/.config/opencode/`,
   `~/.claude/`, `~/.codex/`, …) — state the tool path explicitly, never
   assume one global location.
4. Propose-first on every edit: state the change, its blast radius (every
   session, every repo), and deploy cost (`make dev` + restart). Never
   freestyle edits to a live global file.
5. Shared cookbook — read `./references/agents-md-cookbook.md` for ranked
   content, must-NOT list, budgets, and cut-tests (symlink to the router
   skill's copy; single source, edit either end).

## Review mode

Judge a proposed change SOUND or UNSOUND:

- SOUND: universal across repos, deploy cost stated, budget holds.
- UNSOUND: project specifics leaking upward, personal/secrets content,
   per-language detail, unenforceable prose.
- Cross-project pollution is the top finding — name it when seen. Full
  reasoning whenever the global file is involved, even for approvals.
