# Agentic Planning Workflow

## Purpose
A human-in-the-loop cycle for drafting, auditing, and improving plans using two
specialized subagents. You stay the decision-maker; the agents expose blind spots
and known patterns you may not know exist.

## Prerequisites
- Opencode configured (`.config/opencode/opencode.jsonc` deployed)
- Plan written as a markdown file in the project repo

## The Cycle (one pass)

1. **Draft plan** — you write the plan file
2. **Invoke `@plan-auditor`** — get edge cases, conflicts, assumptions
3. **Update plan** — you revise based on findings you agree with
4. **Invoke `@plan-mentor`** — get pattern recognition, unknown unknowns
5. **Update plan** — you revise based on mentorship you agree with
6. **Invoke `@plan-auditor`** (final pass) — review new findings
7. **Fix only the genuine ones, then move to implementation**

## Important: avoiding the optimization trap

The audit is powered by an LLM — it will always find "problems" because
that is what it was asked to do. A plan with zero audit findings is
unrealistic.

- The first audit catches real blind spots (you haven't reviewed the plan yet)
- The final audit may surface speculative or hallucinated issues
- **Use judgment:** fix only what you genuinely agree is a problem
- Do not re-audit after the final fix — this creates an infinite loop

The goal is **good enough to implement**, not zero findings.

## Subagent: plan-auditor

- Temperature: 0.1
- Permissions: read-only
- Web access: no — pure document analysis

**Invocation:**
```
@plan-auditor docs/<plan-file>.md
```

**Output format:**
```
### Finding: <title>
Category: edge-case | conflict | assumption
Location: <section reference>
Justification:
1. <3 concise phrases>
```

If no plan file is found or the plan is ambiguous, the agent stops and returns
clarifying questions — it never guesses.

## Subagent: plan-mentor

- Temperature: 0.4
- Web access: yes — GitHub, GitLab, forums for verification

**Invocation:**
```
@plan-mentor docs/<plan-file>.md
```

**What it surfaces:**
- **Known patterns** — components that resemble established solutions
- **Industry alternatives** — maintained libraries or tools that replace
  hand-crafted code
- **Fragile idioms** — approaches the model's training has seen fail
- **Unknown unknowns** — aspects you may not have considered

Web search is used for verification, not primary generation.

## Rules enforced by every agent

- **No fabrication** — if the plan file doesn't exist, the agent states so and stops
- **Ambiguity → questions** — unclear plans trigger clarifying questions, not guesses
- **Single pass** — one cycle only. Re-invoke manually for another iteration.

## Example session

```
# You write a draft
$ vim docs/new-feature-plan.md

# Audit it
@plan-auditor docs/new-feature-plan.md

  → 5 findings returned

# Revise the plan, then get mentorship
@plan-mentor docs/new-feature-plan.md

  → "You're implementing X manually — Y exists.
     Your error handling in Z has a known failure mode."

# Revise again, final audit
@plan-auditor docs/new-feature-plan.md

  → 2 new findings — fix the one that matters, ignore the speculative one
  → Start implementation
```

## Future improvements

### File format support
Currently both subagents expect markdown files. Future versions could accept
JSON, YAML, or TOML plan descriptions — useful when the plan is generated
by another tool or needs machine validation.

### Variable plan structure
A full-phase plan (multi-component, cross-cutting) has different structural
needs than a single-feature or refactor plan. Future iterations could:
- Detect scope from the file and adjust the audit depth
- Accept a `--scope` flag (phase | feature | refactor) to tune what the
  agent looks for
- Support inline plans (pasted directly into chat) for quick feedback
  without creating a file

### Auditing pipeline descriptions
If the plan describes an end-to-end pipeline or CI workflow, the auditor
could trace the data flow and flag missing stages, error paths, or
implicit ordering constraints that a static doc structure misses.
