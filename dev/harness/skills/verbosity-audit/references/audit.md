# Audit process

## Baseline

- `wc -l`
- chars/4 token estimate
- `grep -n '^## '`
- long lines: `awk '{ if (length($0) > 600) print NR": "length($0)" chars" }'`
- bullets over ~500 chars

## Classify lines

Use `skill-authoring`: fragile · judgment · explanation · derived · duplicate.

- Cut explanation and derived.
- Merge duplicates.
- Keep fragile lines verbatim, however long.

## No-op test

Delete the line mentally. If behavior or understanding changes, keep it. If unsure, keep it. Settle disputes by running, not arguing.

## Semantic contract

Before cutting, list:

- facts
- constraints
- commands/paths
- edge cases
- warnings/prerequisites
- examples needed to remove ambiguity

Every item must survive or be explicitly replaced.

## Report before editing

Use `file:line` + defect + fix. Present all findings before changes.

## Rewrite

- One pass after approval.
- Preserve conventions: frontmatter, headings, exact terms.
- Brevity is not the sole goal.

## Verify

Independent second pass compares original and revised:

- intent preserved
- exact terms unchanged
- edge cases still handled
- constraints still enforced

Report PASS or REGRESSION. If REGRESSION, revise or keep original.

## Re-measure

- token delta
- justify any increase (examples, motivations, structure)
- stop when clarity stops improving or risk rises
