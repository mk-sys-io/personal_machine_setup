## MCP rationale

### Context7 — always on
The agent hallucinates library APIs from stale training data.
Context7 fetches current version-specific docs at query time.

### Memory MCP — always on
Every session the agent re-explores the repo from scratch.
Memory persists a knowledge graph in `.opencode/memory.jsonl`
so the agent picks up where it left off, per project.

### Memory MCP — known limitations
- No dedup: same observation added twice = two copies
- No update: `add_observations` is append-only; stale
  facts must be deleted before replacement
- No timestamps: no createdAt/lastModified
- No conflict detection: contradictory facts coexist
- Flat strings only: no structured fields or embeddings
