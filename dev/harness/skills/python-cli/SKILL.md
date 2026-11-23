---
name: python-cli
description: Load before creating, changing, or auditing a Python command-line tool — entry point, argparse/click/typer parser, subcommands, flags, prompts, help text, exit codes, output streams. Not for shell scripts, or internal changes that leave the interface untouched.
---

# python-cli

## Build

- Write the `--help` text first: subcommands, every flag, one example. Hard to write ⇒ wrong grammar.
- ≥2 actions on one noun ⇒ subcommands. Otherwise flags. One grammar only, kebab-case.
- Defaults cover the common path; no required flag for it.
- Mutating verbs are explicit (`apply`, `destroy`); default is read-only, or `--dry-run` that runs the real path.
- Copy `references/cli.py`; do not hand-roll a parser. Lint with the `python-lint` skill.

## Audit

- Read source and `--help` only. Run a command only if it is a tool created in this session, or the user authorized it. Otherwise report it unassessed and print the command.
- Never skip a check because the feature is absent — the absence may be the finding.
- Report before changing anything. Interface only: no internal refactors, no flag rename without an alias plus a deprecation line.
- One finding per root cause, ordered by severity:
  `[CRITICAL|HIGH|MEDIUM|LOW] title` · `Where: file:line` · `Evidence:` · `Fix:`
- CRITICAL: destroys data without confirmation · exits 0 after an error · hangs on closed stdin · `--json` emits non-JSON on stdout.
- Hand-off markers: `unassessed (operator-only | needs tty | needs sudo | needs device) — run: <cmd>`.
- A prompt cannot be verified by the model (`isatty` is false here). Check the guard in code; hand the interactive test off.

## Non-negotiables

- `[y/N]`, Enter aborts, accept `y|yes`; never require typing `yes`.
- `--yes` skips confirmation; `--force` overrides a safety refusal. Never aliases.
- No prompt when stdin is not a TTY or when `--json`; exit 2 and print the exact re-run line.
- stdout = data only; stderr = logs, progress, prompts, errors. `--json` ⇒ one document on stdout.
- 0 ok · 1 operational failure · 2 usage · 130 SIGINT. Never 0 after an error.
- Every flag has a long form; `-h/--help` and `--version` exit 0.
- No usage block in the module docstring — cite `--help`.

Checks and provenance: `references/conventions.md`.
