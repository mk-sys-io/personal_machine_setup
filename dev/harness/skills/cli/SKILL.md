---
name: cli
description: Load before creating, changing, or auditing a command-line tool's interface — Python or bash — including subcommands, flags, prompts, help text, exit codes, output streams. Not for modules driven by install.py, or internal changes that leave the interface untouched.
---

# cli

## Build

- Write the `--help` text first: subcommands, every flag, one example. Hard to write ⇒ wrong grammar.
- ≥2 actions on one noun ⇒ subcommands. Otherwise flags. One grammar only, kebab-case.
- Defaults cover the common path; no required flag for it.
- Mutating verbs are explicit (`apply`, `destroy`); default is read-only, or `--dry-run` that runs the real path.
- Copy `references/cli.py` or `references/cli.sh`; do not hand-roll a parser. Lint: `python-lint` / `shellcheck`.

## Audience

- One question sets the tier — does anything you don't control run it? `internal` (default; run by hand, by you) → `shared` (cron, systemd, a script or another person consumes it) → `published` (someone else installs it, or its output is a contract).
- Promote when the first consumer appears. The tier gates every rule in `references/conventions.md`.
- Modules driven by `install.py` are not CLIs: their 0/1/2/3 contract is `lib/README.md`'s.

## Audit

- Read source and `--help` only. Run a command only if it is a tool created in this session, or the user authorized it. Otherwise report it unassessed and print the command.
- State the tier, then check only the rules that apply at it. Never skip an applicable check — the absence may be the finding. On a legacy tool the report is the remediation backlog: fix order, tier-correct.
- Report before changing anything. Interface only: no internal refactors; no flag rename without an alias plus a deprecation line (published only).
- One finding per root cause, ordered by severity:
  `[CRITICAL|HIGH|MEDIUM|LOW] title` · `Where: file:line` · `Evidence:` · `Fix:`
- CRITICAL: destroys data without confirmation · exits 0 after an error · hangs on closed stdin · `--json` emits non-JSON on stdout.
- Hand-off: `unassessed (operator-only | needs tty | needs sudo | needs device) — run: <cmd>`. A prompt cannot be verified here (`isatty` is false); check the guard in code, hand the interactive test off.

## Non-negotiables

Universal at every tier:
- `[y/N]`, Enter aborts, accept `y|yes`; never require typing `yes`.
- `--yes` skips confirmation; `--force` overrides a safety refusal. Never aliases.
- No prompt when stdin is not a TTY or when `--json`; exit 2 and print the exact re-run line.
- stdout = data only; stderr = logs, progress, prompts, errors. `--json` ⇒ one document on stdout.
- 0 ok · 1 operational failure · 2 usage · 130 SIGINT. Never 0 after an error.
- Every flag has a long form; `-h`/`--help` exit 0.
- No usage block in the module docstring — cite `--help`.

Tiered rules: `references/conventions.md` · language mechanics: `references/mechanics.md`.
