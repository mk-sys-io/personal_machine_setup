## MCP rationale

### Context7 — always on
The agent hallucinates library APIs from stale training data.
Context7 fetches current version-specific docs at query time.

### Basic Memory — always on
Every session the agent re-explores the repo from scratch.
Basic Memory persists markdown notes + knowledge graph so
the agent picks up structured context where it left off.

### Storage model
- Notes are plain markdown files in `~/.basic-memory/<project>/`
  with YAML frontmatter, observations, and typed relations
- FTS5 full-text + optional vector (FastEmbed ONNX) hybrid search
- No cloud dependency, no telemetry, no opaque format

### Key tools
- `search_notes` — hybrid search across all stored context
- `write_note` — create a new note with frontmatter + tags
- `read_note` / `edit_note` / `delete_note` — CRUD
- `recent_activity` — what changed since last session
- `build_context` — traverse knowledge graph from a starting note

### Project management
All repos share a single Basic Memory project (`main` at `~/basic-memory/`).
Notes are namespaced by repo name in `search_notes` queries and tags.
This avoids per-repo configuration overhead — no setup needed for new repos.

## Plan Workflow: `/proceed`

Multi-file features use a single command: execute the plan.

**Setup per repo:** `tools/init.sh` creates `plans/` + `.ignore` (`!plans/` enables `@plans/filename` references). Plans are excluded from git via `~/.config/git/ignore` (global). See [plan-workflow.md](docs/plan-workflow.md) for the full guide.

## Dependency Vetting

When the agent suggests a new third-party OSS dependency, the `dep-vet` skill
automatically vets it across 6 activity/security/quality/maturity/community/vibe-code
metrics. See [dep-vet-architecture.md](docs/dep-vet-architecture.md) for the full
architecture, metric thresholds, file inventory, and maintenance guide.

## Custom Commands

| Command | Description |
|---|---|
| `/proceed` | Execute a plan — from a direct `plans/` file or live chat |
| `/seed` | Seed Basic Memory with codebase structure for a new project |
| `/vet` | Vet a third-party OSS dependency before suggesting it |
