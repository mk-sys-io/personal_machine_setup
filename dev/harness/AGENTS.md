## Golden rules

Evidence before synthesis: quote file:line or command output before concluding. Before answering, check the request for false assumptions or misconceptions; correct them. Never guess APIs, versions, paths, or owner/repo — resolve via docs, Context7, or registry spec first. Missing or ambiguous → STOP, state what's missing; never fill gaps. Be honest in reports: flag flaws, risks, and uncertainty. Verify by execution: read the file, run the command; error → fix now, warning → fix or note trade-off. Emit only needed tokens: no preamble/recap; bullets over prose. Keep errors/IDs/numbers verbatim. Expand only when uncertain, ambiguous, flaws found, or user asks why.

## Dependency Vetting

Before suggesting a dep with a public GitHub/GitLab repo: never invent an `owner/repo` — resolve via registry spec or verified URL. The `dep-vet` skill and `dep_vet` tool are no longer shipped; verify activity, security, quality, maturity, and community from primary sources instead of guessing.

## Scratch files

Use `$TMPDIR/opencode` (usually `/tmp/opencode`) for temporary/scratch files outside the workspace — it is the shared, pre-approved scratch dir. OpenCode additionally denies every other `/tmp` path via permission config (`PermissionDeniedError`, no prompt); harnesses without that gate keep the same convention.

## External library docs

When the task involves a library, framework, SDK, plugin/extension, API, or app/tool configuration — docs, code generation, versions, or setup steps — use Context7 when the harness exposes it (`resolve-library-id`, then `query-docs`); otherwise use the official docs via web. Never guess APIs or versions from memory.

## Tool use & uncertainty

- Batch independent tool calls (reads, searches, commands) in parallel; sequence only on dependency.
- Never fabricate facts, files, or output; if uncertain, say so and verify before proceeding.
- Acknowledge uncertainty explicitly before acting on it.

## Skills

Before creating, editing, or reviewing a `SKILL.md` under `dev/harness/skills/`, load the `skill-authoring` skill. Same skill when fixing one that never fires, fires too widely, or has grown too long.

## TypeScript / JavaScript

Before editing or reviewing `.ts/.tsx/.mts/.cts/.js/.jsx/.mjs/.cjs` files — or when a lint, type, or format error appears — load the `typescript-lint` skill.

## Python

Before editing or reviewing `.py`/`.pyi`/`.ipynb` files — or when a lint, type, or format error appears — load the `python-lint` skill.
