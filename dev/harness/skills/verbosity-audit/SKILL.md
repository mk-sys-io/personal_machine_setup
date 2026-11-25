---
name: verbosity-audit
description: Load before creating, editing, reviewing, or trimming agent-facing instructions, skill files, prompts, or documentation when verbosity, bloat, token cost, or readability is in question. Not for reference accuracy, safety-critical content, or generated API docs.
---

# Verbosity audit

A skill is judgment loaded on trigger. Cut noise, not meaning.

## Decide first

- Classify the file type before cutting; type gates the rules.
- If the file is a `SKILL.md`, satisfy `skill-authoring` limits first.
- If the target is reference, safety-critical, or generated docs, trim filler only.

## Audit

- Measure baseline: lines, chars/4 token estimate, section map, long lines/bullets.
- Classify every line: fragile · judgment · explanation · derived · duplicate.
- Cut explanation and derived; merge duplicates; keep fragile lines verbatim.
- Run the no-op test for each candidate cut.
- Preserve the semantic contract: facts, constraints, examples, edge cases, warnings.

## Rewrite

- Present findings with `file:line` + defect + fix before editing.
- Rewrite in one pass after approval.
- Re-measure and justify any token increase.

## Verify

- Independent second pass compares intent, exact terms, edge cases.
- Report PASS/REGRESSION.

Full rules: `references/audit.md`, `references/file-types.md`.
