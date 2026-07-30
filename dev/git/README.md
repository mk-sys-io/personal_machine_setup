# git/ — Git workflow automation

Configuration and tools for git workflow: global ignore rules, global hooks, per-repo secret scanning.

## Files

### `ignore`
Global gitignore — excludes `plans/` from all repos so plan files aren't committed.
Deployed to `~/.config/git/ignore` by `lib/50-github_setup.sh`.

See [plan-workflow.md](../opencode/docs/plan-workflow.md) for the dual-ignore design
(`.ignore` for ripgrep inclusion, global gitignore for git exclusion).

### `pre-commit`
Global git pre-commit hook — runs `gitleaks protect --staged` on every commit.
Deployed to `~/.git-hooks/pre-commit` by `lib/50-github_setup.sh` with
`core.hooksPath` configured globally.

Gitleaks must be installed (see `packages/apt.txt`). No per-repo hook config needed —
the global hooksPath covers all repos automatically.

### `gitleaks.toml` (template)
Per-repo gitleaks config. Gitleaks is a secret scanner that prevents committing
credentials, tokens, and keys. The template extends gitleaks' 160+ default rules
and adds allowlists for common false positives (template files, example configs).

## Setup on a new project

```bash
tools/init.sh
```

Run from the target repo root. This scaffolds:
- `plans/` — opencode plan files (git-excluded, ripgrep-visible)
- `.ignore` — `!plans/` re-include for `@plans/` references
- `.gitleaks.toml` — secret scanning config with `title` set to your repo name

To customize gitleaks rules after setup, edit `.gitleaks.toml` — add per-path
allowlists for test fixtures, docs, or custom token patterns.
