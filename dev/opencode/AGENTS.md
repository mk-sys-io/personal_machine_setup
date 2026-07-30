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
