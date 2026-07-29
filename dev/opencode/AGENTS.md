## Memory persistence

### On session start
Search Memory MCP for entities named after the current
repo or its key components. Use that context to orient.

### Before writing to memory
Memory MCP has no automatic dedup, update, or conflict
detection. Follow this pattern:
1. `open_nodes` or `search_nodes` for the target entity
2. Read existing observations, check for conflicts
3. If any existing observation is stale or incorrect,
   `delete_observations` the old one first
4. Then `add_observations` with the new fact

### When to save
Save observations during the session for:
- Repo structure and architecture
- Build, test, lint, typecheck commands
- Key decisions and rationale
- Unusual conventions or gotchas

## Context efficiency

Tool results (search, grep, file reads, web fetch) often
produce more text than needed. When a tool returns verbose
output, summarize the relevant parts instead of quoting
the full result verbatim. This keeps the context lean and
focused on the current task.
