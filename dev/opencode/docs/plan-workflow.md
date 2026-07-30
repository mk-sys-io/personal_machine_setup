# Plan Workflow: `/brief` → `/proceed`

A two-command workflow for implementing multi-file features: preview the plan, then execute with discipline.

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
    ├── feature-name.md            # Entrypoint (read by /brief)
    ├── phase1.md
    └── phase2.md
```

Effective plans include:
- Numbered implementation steps
- File paths for what to change
- Acceptance criteria or commands to verify

## `/brief <name>`

Reads the plan and presents the first logical chunk for review.

- `agent: plan` — read-only, no file changes possible
- Resolves `plans/<name>.md`, or `plans/<name>/` directory
- Output: first 3 steps + target files + approach + commands

```
/brief dep-vet-plan
→ "First 3 steps: 1. Resolve repo URL…
   Files: AGENTS.md, SKILL.md
   Commands: trustoss analyze, curl…
   Review above. Type /proceed to implement."
```

## `/proceed`

Executes a plan. Resolves in this priority order:

1. **Direct arg** — `/proceed dep-vet-plan` reads `plans/dep-vet-plan.md` and executes
2. **After `/brief`** — executes the plan from the most recent `/brief` output
3. **Live chat** — executes agreed implementation steps from conversation
4. **Nothing** — states "Nothing to proceed on" and stops

- `agent: build` — switches from plan mode automatically
- `todowrite` per step, scope discipline, stop-and-report on failure

## Example session

```
Start a new feature that touches multiple files:

  /brief my-feature
  → Plan presented + confirmed

  /proceed
  → Step 1 in_progress → completed
  → Step 2 in_progress → completed
  → Step 3: failed — report and stop

Fix the issue, then /proceed again to continue.
```

## Tips

- Keep plan steps small enough for one todowrite entry each
- If a step is ambiguous, instruct the agent to ask rather than guess
- For trivial changes, skip the workflow — go directly to Build mode
