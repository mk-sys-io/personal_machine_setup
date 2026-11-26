---
name: python-lint
description: Load before editing or reviewing .py, .pyi, or .ipynb files, and whenever a Python lint, type, or format error appears.
---

# Python lint / format / typecheck ladder

Mutating (apply fixes):

1. `ruff check --fix <paths>`
2. `ruff format <paths>`
3. `basedpyright <paths>` (no autofix)

Check-only:

1. `ruff check <paths>`
2. `ruff format --check <paths>`
3. `basedpyright <paths>`
On demand (repo-wide dead code): `uvx --quiet vulture --min-confidence 100 .` (exit 3 = findings)

## How to read diagnostics

- ruff: errors block; `--fix` applies safe fixes only; unsafe fixes need `--fix --unsafe-fixes` + review.
- basedpyright: errors block; warnings advisory (fix or note the trade-off). Repo uses `standard` mode (`pyrightconfig.json`); with no config it falls back to `recommended` — run from the repo root so the repo config applies.
- Import sorting comes from the `I` rules in step 1.

## Gotchas

- Commit hook now runs `ruff check --fix` + `ruff format` on staged `.py`/`.pyi`/`.ipynb` and `basedpyright` when a project config exists (`dev/git/pre-commit`); editor save still does not, so this skill remains the manual/agent gate.
- Ruff config is machine-wide (`dev/ruff/pyproject.toml` → `~/.config/ruff/pyproject.toml`); a repo-local config overrides it.

## Non-negotiables

- No `Any`/`cast` laundering; no bare `# type: ignore`; no broad `# ruff: noqa` without a code + reason.
- Never edit ruff/pyright config or lower thresholds to pass.
- Never delete or skip tests.
- If a fix changes runtime behaviour, stop and explain.
