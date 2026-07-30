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
