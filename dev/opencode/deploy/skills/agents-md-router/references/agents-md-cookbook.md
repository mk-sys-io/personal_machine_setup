# AGENTS.md Cookbook (shared core)

Single source for both authoring skills. Per-scope deltas live in each
SKILL.md; this file holds what is identical. Core written once — never
duplicate lines between here and the skills. Shared into
`global-agents-md/references/` via relative symlink — edit either end,
single source; never replace the link with a copy.

## Content, ranked by behavior-change-per-token

1. Exact verified commands (copy-pasteable; non-default tooling only).
2. Never-paired-with-alternative (`don't X; do Y` — bare don'ts cause
   over-exploration). Tiers: ALWAYS / ASK FIRST / NEVER.
3. Verify tiers with one pre-push gate: fast → unit → integration.
4. Ownership/tool routing (`area → path`, which script or tool to use).
5. Non-obvious gotchas + env quirks (names, never values).
6. Thin session-start routine, 4–8 lines max.
7. Recurring review/commit etiquette only — add a rule when the same
   comment is left twice.

## Include/exclude heuristic

Include what the agent can't guess, what differs from defaults, and what
is verifiable via command, glob, or path. Exclude what it infers (API
docs, file trees, history), what it knows (language conventions, "clean
code"), and aspirational unenforced prose or taste words.

## Must-NOT list

No encyclopedia dumps. No per-language detail in global files (scope via
`paths:`/`globs:` or linked docs). No linter's-job prose. No stale
specifics (update in the same PR as the convention change). No line-number
anchors as sole reference. No hard byte-count rules in-file. No ALL-CAPS
on every bullet. No secrets, env values, or customer data, ever. No file
trees or structure inventories. No divergent duplicates — one fact, one
place.

## Reference mechanics

`file:line`/symbol pointers over pastes (pastes rot). Nested files for
genuinely different commands per package. Path-scoped rules over root
growth. Procedures with side effects become skills, not root prose.
`AGENTS.md` canonical; `CLAUDE.md` = import/symlink + deltas.

## Detection ladder (split by scope)

Local file hunts, in order: repeated agent mistakes → recurring reviewer
comments → env/tool quirks breaking fresh sessions → stale specifics.
Global file hunts: project content leaking upward (top finding) →
personal/secrets content → per-language detail → unenforceable prose.
Each finding states where the fix lives: root line, scoped rule, skill,
or hook — never more prose.

## Advisory budgets (lines, never bytes)

Root ≤150 lines. Top prohibition ≤line 30. ~150–200 followable
instructions total including harness. Scoped rule files <500 lines. Every
"don't" paired. Guidance with the cut-test, not gates.

## MCP server nudges

One section per server in the global file, nowhere else (global loads once
per session everywhere; tool schemas re-sent every turn are the real cost —
gate new servers by tool count, not nudge count). Trigger format: Always/when
+ named literal tools + ordering (before answering) + anti-memory clause
(never guess from memory); scope names the concept class broadly (libraries,
frameworks, SDKs, plugins/extensions, configs — not just 'API'). Each nudge ships with its cold-session verify
(external-lib question fires, ordinary question silent). Row-worthiness test
gates additions; compliance degrades past ~200 lines — reassess, never stack.
First instance: Context7 section in the global file (resolve → query).

## Pre-commit checklist + cut-tests

`git status --short`, `git diff --stat`, gitleaks clean, commands run
verbatim before proposing commit. Cut-tests per line:
"would-removal-cause-mistake?"; every command traces to a real definition;
no freestyle — load the skill.
