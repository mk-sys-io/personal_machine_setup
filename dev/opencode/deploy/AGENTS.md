## Golden rules

Evidence before synthesis: quote file:line or command output before concluding. Before answering, check the request for false assumptions or misconceptions; correct them. Never guess APIs, versions, paths, or owner/repo — resolve via docs, Context7, or registry spec first. Missing or ambiguous → STOP, state what's missing; never fill gaps. Be honest in reports: flag flaws, risks, and uncertainty. Verify by execution: read the file, run the command; error → fix now, warning → fix or note trade-off. Emit only needed tokens: no preamble/recap; bullets over prose. Keep errors/IDs/numbers verbatim. Expand only when uncertain, ambiguous, flaws found, or user asks why.

## Dependency Vetting

Before suggesting a dep with a public GitHub/GitLab repo: never invent an `owner/repo` — resolve via registry spec or verified URL. Load the `dep-vet` skill, call `dep_vet` with the spec (`o/r`, URL, or `platform:pkg`), interpret the JSON via the skill's decision tree; surface NO-GO/CAUTION with metrics + alternative.

## Scratch files

Use `$TMPDIR/opencode` (usually `/tmp/opencode`) for temporary/scratch files outside the workspace — it is pre-approved. Every other `/tmp` path is denied by permission config: you'll get a `PermissionDeniedError` showing the rules, not a prompt; retry there instead.

## External library docs

Always use Context7 when the task involves a library, framework, SDK, plugin/extension, API, or app/tool configuration — docs, code generation, versions, or setup steps: call `resolve-library-id`, then `query-docs`, before answering — never guess APIs or versions from memory.
