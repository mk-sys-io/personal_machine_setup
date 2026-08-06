## Memory (Basic Memory MCP)

### Session start
Call `search_notes` for this repo name + key modules to orient.
Call `recent_activity` to see what changed since last session.

### What to store
- **Decisions** — after making one, write a note with rationale and rejected alternatives
- **Architecture** — module structure, key files, dependency flow (one-time per module)
- **Blockers** — when stuck, log the hypothesis chain so next session picks up mid-stream
- **Conventions** — naming, patterns, gotchas discovered during work
- **Project maps** — entrypoints, build commands, test locations (one-time setup)

### How to write
Use YAML frontmatter (`title`, `tags`, `type`) on every note.
Use `- [category] observation text` for atomic facts.
Use `relates_to [[Other Note]]` for cross-links.

### Retrieval before action
Before making significant changes, `search_notes` + `build_context` for relevant prior context. Don't trust your training data over stored memory.

### Storage rules
Skip: transient debug output, chat chitchat, obvious API docs.
Basic Memory handles dedup and updates natively — no manual conflict management.

## Language Server / Diagnostics

- LSP (ruff + basedpyright, `typeCheckingMode: standard`) injects lint/type diagnostics after file edits — treat them as authoritative.
- If the LSP is inactive, fall back to `ruff check <file>` and `python3 -m basedpyright <file>`.

### Diagnostic tiers
- error → fix now
- warning → fix unless a deliberate trade-off (note it)
- information / hint → tolerate; leave as-is

### Rules
- Run lint/type checks on edited files after editing: `ruff check --fix` (safe fixes only), `basedpyright`, `bash -n` + `shellcheck`.
- Don't bulk-fix unrelated code; keep edits scoped to the task at hand.
- Tolerance changes (ruff/shellcheck/pyright configs) are user decisions — propose, don't apply.

## Dependency Vetting

Before suggesting a dep with a public GitHub/GitLab repo, load `dep-vet`:

- Never invent an `owner/repo`; resolve via registry spec or verified URL (skill: dep-vet).
1. Call the `dep_vet` tool with the dep spec (`o/r`, URL, or `platform:pkg`)
2. Interpret the structured JSON via the skill's decision tree → verdict
3. On NO-GO/CAUTION: flag prominently, explain which metrics, suggest alternative
