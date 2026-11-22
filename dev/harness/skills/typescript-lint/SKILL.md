---
name: typescript-lint
description: Load before editing or reviewing .ts/.tsx/.mts/.cts/.js/.jsx/.mjs/.cjs files, and whenever a lint, type, or format error appears.
---

# TypeScript lint / format / typecheck ladder

1. Format: `biome check --write <paths>`.
2. Typecheck: `tsc --noEmit -p <tsconfig dir>` (the only automatic type gate is the git hook; this is the on-demand run).
3. Typed lint (on demand): `oxlint --type-aware --deny typescript/no-unsafe-assignment --deny typescript/no-unsafe-call --deny typescript/no-unsafe-member-access --deny typescript/no-unsafe-return --deny typescript/no-unsafe-argument --deny typescript/no-unsafe-type-assertion --deny typescript/no-floating-promises --deny typescript/no-misused-promises <paths>`
   Requires a repo `tsconfig.json` (auto-discovered; `--tsconfig` to override). Never on save; never in the commit hook.
   Keep the deny list in sync with `docs/typescript-tooling.md` §Tier policy, Typed.

## Gotchas
- No automatic type gate unless you commit: the hook runs `tsc` only with a root `tsconfig.json` and installed deps (`node_modules`/`.pnp.cjs`, or no `package.json`) — otherwise it silently skips (`dev/git/pre-commit:33-34`).
- Save runs `biome format` only; no lint or typecheck runs until you run this ladder.

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
