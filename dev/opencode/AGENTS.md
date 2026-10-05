## Dependency Vetting

Before suggesting a dep with a public GitHub/GitLab repo: never invent an `owner/repo` — resolve via registry spec or verified URL. Load the `dep-vet` skill, call `dep_vet` with the spec (`o/r`, URL, or `platform:pkg`), interpret the JSON via the skill's decision tree; on NO-GO/CAUTION flag prominently, explain which metrics, suggest an alternative.

## Scratch files

Use `$TMPDIR/opencode` (usually `/tmp/opencode`) for temporary/scratch files outside the workspace — it is pre-approved. Every other `/tmp` path is denied by permission config: you'll get a `PermissionDeniedError` showing the rules, not a prompt. Don't retry other `/tmp` locations; retry there instead.

## External library docs

Always use Context7 when the task involves a library, framework, SDK, plugin/extension, API, or app/tool configuration — docs, code generation, versions, or setup steps: call `resolve-library-id`, then `query-docs`, before answering — never guess APIs or versions from memory.
