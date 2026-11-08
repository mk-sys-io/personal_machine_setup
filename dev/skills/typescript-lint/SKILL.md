---
name: typescript-lint
description: Load before editing or reviewing .ts/.tsx/.mts/.cts/.js/.jsx/.mjs/.cjs files, and whenever a lint, type, or format error appears.
---

# TypeScript lint / format / typecheck ladder

1. Format: `biome check --write <paths>` (safe fixes + import order).
2. Typecheck: `tsc --noEmit -p <tsconfig dir>` (the only automatic type gate is the git hook; this is the on-demand run).
3. Typed lint: `oxlint --type-aware` (exact rule set added in Phase 8 — run the command from `docs/typescript-tooling.md`).

## How to read diagnostics

- Errors block; warnings are advisory (cognitive complexity, `!` assertions). Fix errors; leave warnings unless they hide a defect.
- `biome` diagnostics name the rule; look up the rule before suppressing.
- `tsc` errors are authoritative for types — never silence with `any` or `@ts-ignore`.

## Non-negotiables

- No `any`, no `as unknown as` laundering, no `void` to silence floating promises (use `await` or `.catch`).
- No `@ts-ignore` / `@ts-expect-error` without a reason comment and only for third-party bugs.
- Never disable a rule, edit a lint config, or lower a threshold to make a check pass.
- Never delete or skip tests to make a check pass.
- If a fix changes runtime behaviour, stop and explain instead of committing.