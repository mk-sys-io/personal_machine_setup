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

- Markdown is checked by the global pre-commit hook on commit: structural issues (headings, fences, lists, tables, links) block; cosmetic issues warn. Fix reported errors; do not "fix" disabled rules (no line wrapping, no added headings, no table padding).
- Pre-commit check without committing: `npx --yes markdownlint-cli2@0.24.2 --no-globs --config "$HOME/.config/markdownlint/.markdownlint.jsonc" -- <paths>`
