---
description: Seed Basic Memory with codebase structure for a new project
agent: build
---

You are seeding Basic Memory for this project.

1. Call `search_notes` with this repo name and key terms. If meaningful results exist, summarize what's already known and stop — memory is already seeded.

2. If memory is empty, explore the codebase:
   - Top-level file listing, README, package.json, tsconfig, any docs/ or GUIDELINES
   - Entrypoints, main src/ structure, key modules (depth: ~3 levels, not every file)
   - Any existing configuration files, CI setup, test structure
   - Use `context7` to fetch docs for any major frameworks/libraries used

3. Write structured notes via `write_note`:
   - One architecture note: module layout, entrypoints, dependency flow, build commands
   - One conventions note: naming, patterns, testing approach, lint rules
   - Key config files summary (list what they control)

4. Print a summary of what was stored.

Scope: informative, not exhaustive. ~15-30 files max. Skip node_modules, .git, build artifacts.
