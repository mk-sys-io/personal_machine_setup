---
name: docs-writing
description: Load before creating, editing, or reviewing README files, anything under docs/ (including ADRs and backlog notes), or plan files, and whenever evaluating existing documentation for accuracy, structure, or tone.
---

# docs-writing

## Write
- Write for a reader who has not seen the conversation: define terms, state assumptions.
- Quote evidence verbatim (file:line, command output) before a conclusion; write "unknown" rather than filling gaps.
- Keep paths, commands, and IDs verbatim — no paraphrasing inside code spans.
- Match the surrounding doc's structure and heading style before inventing new sections; heading hierarchy monotonic, one top-level heading.

## Review
- Check every command, path, and target name against the repo; run the cheap ones.
- Flag stale facts (renamed scripts, moved paths, outdated policies) as errors, not style issues.
- Reject: walls of prose, duplicated sections, contradictions with AGENTS.md files.

## Markdown
- After editing repo markdown, run `make md-lint` and fix reported errors. Rules disabled in `.markdownlint-cli2.jsonc` are intentional — do not "fix" them (no wrapping lines, no added headings).