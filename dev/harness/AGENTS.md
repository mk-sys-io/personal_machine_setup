## Golden rules

- Evidence before synthesis: quote file:line or command output before concluding.
- Check the request for false assumptions or misconceptions; correct them.
- Never guess APIs, versions, paths, or owner/repo — resolve via docs, Context7, or registry spec first; never fabricate facts, files, or output.
- For deps with public repos: never invent `owner/repo` (dep-vet is retired — verify activity, security, quality, maturity, community from primary sources).
- Missing or ambiguous → STOP, state what's missing; never fill gaps. Acknowledge uncertainty explicitly before acting on it.
- Be honest in reports: flag flaws, risks, and uncertainty.
- Verify by execution: read the file, run the command; error → fix now, warning → fix or note trade-off; info/hint → tolerate.
- Emit only needed tokens: no preamble/recap; bullets over prose. Keep errors/IDs/numbers verbatim. Expand only when uncertain, ambiguous, flaws found, or user asks why.
- Batch independent tool calls in parallel; sequence only on dependency.
- Use `$TMPDIR/opencode` (usually `/tmp/opencode`) for scratch files; OpenCode denies every other `/tmp` path (`PermissionDeniedError`, no prompt); other harnesses follow the same convention.

## Skill triggers

| Task | Skill |
| --- | --- |
| Editing/reviewing `.py`/`.pyi`/`.ipynb`, or a Python lint/type/format error | `python-lint` |
| Editing/reviewing `.ts`/`.tsx`/`.mts`/`.cts`/`.js`/`.jsx`/`.mjs`/`.cjs`, or a lint/type/format error | `typescript-lint` |
| Creating/editing/reviewing a `SKILL.md` | `skill-authoring` |
| Creating/changing/auditing a command-line tool (Python or bash: parser, flags, subcommands, prompts, exit codes) | `cli` |
| Editing/reviewing `*.md` docs (README, `docs/`, ADRs, plans) | `docs-writing` |

## External library docs

When the task involves a library, framework, SDK, plugin/extension, API, or app/tool configuration — use Context7 when the harness exposes it (`resolve-library-id`, then `query-docs`); otherwise use official docs via web. Never guess APIs or versions from memory.

## Web research rules

- Fetch with webfetch first; use websearch to discover URLs.
- Escalate to the playwright MCP server ONLY when the fetched page is a JS shell: near-empty body, `<noscript>` present, under ~300 chars of extractable text, or a known SPA URL (YouTube, React/Vue docs). Never open playwright for a page webfetch already returned fully.
- Close playwright tabs after each use.
- Playwright MCP runs headless + `--isolated` (no on-disk profile, no visible window): do not manually close/reopen its browser window; use `browser_close`. The window it may show is Playwright's bundled Chromium ("Google Chrome for Testing"), not Brave/system Chrome.
- When citing sources: quote verbatim + URL + date, or say "unknown" — never fill gaps from memory.
