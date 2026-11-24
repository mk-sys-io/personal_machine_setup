# Postmortem — Deprecated OpenCode Assets

Removed from `dev/opencode/deploy/`: the agent-specific skill, tool, and command trees that
were OpenCode-only and are no longer shipped. Each entry below is a one-liner describing the
deprecated asset.

## Deprecated skills

- `agents-md-router` — authors/reviews repo-local AGENTS.md orientation routers (author + review modes).
- `dep-vet` — vets OSS dependencies across 6 metrics via the `dep_vet` tool; produces a verdict.
- `global-agents-md` — authors/reviews global user-level agent instruction files.

## Deprecated tools

- `dep_vet.py` — stdlib REST data collector for dep-vet metrics (GitHub, Scorecard, OSV, etc.).
- `dep_vet.ts` — OpenCode custom-tool wrapper (Zod args) invoking `dep_vet.py`.

## Deprecated commands

- `vet.md` — `/vet` command invoking the `dep_vet` tool + dep-vet skill workflow.

## Restorability

All content is preserved in git history. Recover any file with `git show HEAD:<path>` (before this
change is committed) or from the pre-deletion commit afterward, e.g.
`git checkout <pre-deletion-commit> -- dev/opencode/deploy/skills/`. Nothing was destroyed.
