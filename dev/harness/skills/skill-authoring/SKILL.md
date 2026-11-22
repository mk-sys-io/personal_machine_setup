---
name: skill-authoring
description: Load before creating, editing, or reviewing a SKILL.md, and when a skill fires too widely, never fires, or has grown past its purpose.
---

# Authoring a skill

A skill is judgment loaded on trigger. It is not a tutorial and not a reference file.

## Decide first — if any answer is no, stop
- One trigger. Several phases are fine when they share it; two intents need two skills.
- Adds something the model cannot already derive: a repo path, a threshold, a sequence, a divergence from a tool default.
- Not needed on nearly every task. If it is, it belongs in `AGENTS.md` instead.

## Write
- Description: imperative trigger sentence — "Load before X, and whenever Y." Name every in-scope use case and the scope exclusion. It is the only thing advertised, so it is the only thing that makes the skill fire.
- Body: rules and constraints. A command ladder of three steps is fine; flag lists, tutorials and option menus are not.
- Never restate a number, table or invariant that has one source of truth (a manifest, `--help`, another file). Cite it; it will drift.
- Irreversible or expensive facts belong in the body under `## Gotchas`, never only in a reference. This overrides the rule above when the fact also lives elsewhere.

## Split
- Body ≤ 40 lines. Format rules, spec tables and links go in `references/format.md`, linked from the body, one level deep.
- Over the limit, move content to a reference — never delete a fragile line to fit.
- Reference files over 100 lines get a table of contents.

## Non-negotiables
- Never duplicate `AGENTS.md` text. Never summarise a reference file's invariants — cite them.
- Name = directory, lowercase kebab, no consecutive hyphens. Full rules: `references/format.md`.
- Delete or shrink; do not append. A skill that grows is a reference file forming.

## Review an existing skill
- Classify every line before cutting: fragile (exact command, threshold, irreversible warning, gotcha) · judgment (rule plus its reason) · explanation · derived · duplicate.
- Cut explanation and derived; merge duplicates; keep fragile lines verbatim however long they are.
- For each judgment line ask: "Would the model get this wrong without it?" No → cut. Unsure → keep.
- Re-read after cutting and confirm every failure mode still has a guard; restore any guard the cut removed.
- Report findings as `file:line` + defect + fix, not as rewritten prose.

## Self-check before you finish
- Body ≤ 40 lines (`sed -n '/^# /,$p' SKILL.md | wc -l`); whole skill ≤ 140 (`cat SKILL.md references/*.md | wc -l`).
- Description names a trigger and a scope exclusion, with no "I can help" / "you can use".
- No derived counts, dates or version numbers in the body.
- Every reference file is linked from the body and sits one level deep.
