## Memory (Basic Memory MCP)

- **Session start:** `search_notes` for this repo name + key modules to orient; `recent_activity` for what changed since last session.
- **Store:** decisions (rationale + rejected alternatives), architecture (one-time per module), blockers (hypothesis chain), conventions, project maps (entrypoints/build/test). Skip transient debug output, chitchat, obvious API docs — dedup is native.
- **Format:** YAML frontmatter (`title`, `tags`, `type`); `- [category] observation text` for atomic facts; `relates_to [[Other Note]]` cross-links.
- **Before significant changes:** `search_notes` + `build_context` for prior context — don't trust training data over stored memory.

## Language Server / Diagnostics

- LSP (ruff + basedpyright, `typeCheckingMode: standard`) injects diagnostics after edits — treat as authoritative; if inactive, fall back to `ruff check <file>` + `python3 -m basedpyright <file>`.
- Tiers: error → fix now; warning → fix unless deliberate trade-off (note it); information/hint → tolerate.
- After editing: `ruff check --fix` (safe fixes only), `basedpyright`, `bash -n` + `shellcheck`. Keep edits scoped — no bulk-fixing unrelated code. Tolerance changes (ruff/shellcheck/pyright configs) are user decisions — propose, don't apply.

## Dependency Vetting

Before suggesting a dep with a public GitHub/GitLab repo: never invent an `owner/repo` — resolve via registry spec or verified URL. Load the `dep-vet` skill, call `dep_vet` with the spec (`o/r`, URL, or `platform:pkg`), interpret the JSON via the skill's decision tree; on NO-GO/CAUTION flag prominently, explain which metrics, suggest an alternative.

## Scratch files

Use `$TMPDIR/opencode` (usually `/tmp/opencode`) for temporary/scratch files outside the workspace — it is pre-approved. Every other `/tmp` path is denied by permission config: you'll get a `PermissionDeniedError` showing the rules, not a prompt. Don't retry other `/tmp` locations; retry there instead.
