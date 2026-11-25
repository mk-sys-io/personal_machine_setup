# File-type rules

## Always-loaded agent files

`AGENTS.md`, `CLAUDE.md`: target ~150-200 lines / ~2-2.5k tokens; ceiling ~300. Cut generic advice, code style, directory listings, exploration guidance, duplicated docs. Keep non-obvious invariants, exact commands, hard rules with alternatives.

## Skill files

- Description: shortest precise trigger; narrow scope.
- Body: minimal router; move depth to references.
- References: cite, don't restate; >100 lines needs TOC.
- Satisfy `skill-authoring` limits before this skill's style rules.

## Prompts

Smallest contract preserving outcome, constraints, output shape, edge cases. Prefer model settings over repeated verbosity instructions. Examples only for ambiguity or edge cases.

## Documentation by Diataxis type

- Tutorial: guided path; minimal explanation needed to proceed.
- How-to: exact steps; no conceptual detours.
- Reference: completeness first; cut narrative filler only.
- Explanation: rationale, tradeoffs, context; no procedure.
- README/landing: orientation and links; no architecture dumps.
- Runbook: steps, checks, rollback, warnings.
- ADR: decision, context, consequences.
- Changelog: not a trimming target.

## Edge cases

- Safety-critical content: trim filler only; never warnings or prerequisites.
- Generated docs: trim, but do not auto-generate whole files.
- Global vs nested `AGENTS.md`: splitting global files saves little context; value is maintenance.
- Token counter gaming: long unwrapped lines hide bloat; measure chars/4, not just lines.
- Model drift: revisit rules per model; stronger models need less scaffolding.
- Already optimal: acknowledge; suggest only minor refinements.
- Large file: iterative mode: structural, then content, then polish.
- Regression: report and offer to keep original.
