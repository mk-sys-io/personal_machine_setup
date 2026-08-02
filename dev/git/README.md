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

## gtr (git worktree manager)

Git worktree manager for running multiple OpenCode panels in parallel.
Each panel gets its own isolated directory and branch without duplicating
the repository. Prevents file conflicts between concurrent AI agents.

**Installed via:** `lib/50-github_setup.sh` (clones to `~/.local/share/gtr`, runs gtr's own `install.sh`)

**Alias:** `g` function in `dotfiles/bashrc` — creates worktree + launches
OpenCode in one command. Shell integration adds tab completion and `gtr cd`
interactive worktree picker.

### Quick start

Verify global config (done by lib/50-github_setup.sh):

```bash
git gtr config list --global
```

Create worktree + launch OpenCode (from g alias):

```bash
g feature/auth
```

Override for a specific repo:

```bash
cd ~/special-repo
git gtr config set gtr.ai.default pi
```

See all worktrees:

```bash
git gtr list
```

Clean up when done:

```bash
git gtr rm feature/auth                   # worktree only (branch preserved)
git gtr rm feature/auth --delete-branch   # worktree + branch
```

### Shell integration (optional)

Added to `dotfiles/bashrc` — deployed to `~/.bashrc` via Makefile. Provides
tab completion and `gtr cd` interactive worktree picker.

See [gtr docs](https://github.com/coderabbitai/git-worktree-runner) for full
configuration reference.

### Note on Zed windows

Each gtr worktree is a separate Git working tree. Zed's Git panel only reflects
the project root — it does not update when you switch terminal tabs. To see
per-branch Git status, open each worktree in a separate Zed window
(`git: worktree` or open the worktree folder directly).

#### Switching between worktrees

Git enforces single-checkout: a branch can only be checked out in one worktree
at a time. This applies to all branch-switching commands — `git switch`,
`git checkout`, and Zed's branch picker all emit the same error:

```
fatal: '<branch>' is already used by worktree at '...'
```

Use Zed's **worktree picker** instead (`git: worktree` or `alt-ctrl-shift-w`).
This lists all worktrees and switches between them without hitting the
single-checkout constraint.
