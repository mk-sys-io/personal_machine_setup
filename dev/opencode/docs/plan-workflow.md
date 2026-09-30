# Plan Workflow: `:run`

An espanso trigger for implementing multi-file features: execute the plan with discipline.

## When to use

Multi-file features, refactors, or architectural changes where the agent should not drift. For single-file edits or trivial fixes, skip this — just describe what you need directly.

## Setup

Every repo needs a `plans/` folder and dual-ignore configuration:

```bash
# One-time per repo
tools/init.sh
```

This creates:
- **`plans/`** — directory for implementation plan files
- **`.ignore`** with `!plans/` — re-includes `plans/` for ripgrep (enables `@plans/filename` references in opencode)

Plans are excluded from git globally via `~/.config/git/ignore` (content: `plans/`). This is Git's XDG default excludes file — no local `.gitignore` entry needed.

The dual-ignore design solves a specific problem: adding `plans/` to `.gitignore` would cause ripgrep (and opencode's `@` syntax) to skip it. The `.ignore` file with `!plans/` overrides the git exclusion for ripgrep without affecting git's behavior.

| File | Role | Scope |
|---|---|---|
| `~/.config/git/ignore` | Exclude `plans/` from git | Global — all repos |
| `.ignore` (`!plans/`) | Re-include for ripgrep | Per repo (created by `init.sh`) |

### Why not just use `.gitignore`?

**Any** git ignore mechanism — `.gitignore`, `~/.config/git/ignore` (global), or `.git/info/exclude` (per-repo) — is also respected by ripgrep. Adding `plans/` to any of them breaks opencode's `@plans/` file references even though git would see the exclusion correctly.

ripgrep's `.ignore` file is the inverse: git ignores it entirely, but ripgrep reads it and its `!` negation overrides git's ignore rules. That is why the dual-ignore approach exists — each tool reads only one of the two files, and neither tool sees the other's rule.

## Plan files

Place markdown plan files in `plans/`. For complex features with multiple phases, use a subdirectory:

```
plans/
├── feature-name.md                # Single file
└── large-feature/
    ├── feature-name.md            # Entrypoint
    ├── phase1.md
    └── phase2.md
```

Effective plans include:
- Numbered implementation steps
- File paths for what to change
- Acceptance criteria or commands to verify

## `:run`

Fires the plan-execution prompt. The plan is located in the assistant's own recent output, not in this prompt.

1. Locate the plan in YOUR OWN earlier output: read your most recent response, then the one before it. The plan is not in this prompt — do not search here for it.
2. If neither of those has a plan, reply "Nothing to proceed on" and stop.
3. Never invent content. If the plan is missing, or key decisions are still ambiguous, STOP and say plainly what is missing or undecided. Do not guess, do not fill gaps.

**Precondition:** switch to build mode manually before firing `:run`. The old `/proceed` command enforced `agent: build` automatically; the espanso trigger cannot, so the mode switch is now your responsibility.

- `todowrite` per step, scope discipline, stop-and-report on failure

## Example session

```
You and the agent agree on a plan in chat. The agent's last response contains the numbered steps. Switch to build mode, then fire :run.

  :run
  → Step 1 in_progress → completed
  → Step 2 in_progress → completed
  → Step 3: failed — report and stop

Fix the issue, then fire :run again.
```

## Tips

- Keep plan steps small enough for one todowrite entry each
- If a step is ambiguous, instruct the agent to ask rather than guess
- For trivial changes, skip the workflow — go directly to Build mode
