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
```

```bash
git gtr config set gtr.ai.default pi
```

See all worktrees:

```bash
git gtr list
```

Clean up when done:

Remove worktree only (branch preserved):

```bash
git gtr rm feature/auth
```

Remove worktree and branch:

```bash
git gtr rm feature/auth --delete-branch
```

### Shell integration (optional)

Added to `dotfiles/bashrc` — deployed to `~/.bashrc` via Makefile. Provides
tab completion and `gtr cd` interactive worktree picker.

See [gtr docs](https://github.com/coderabbitai/git-worktree-runner) for full
configuration reference.

### Copying ignored files into worktrees

Gitignored/untracked files aren't part of any commit, so a freshly created
worktree — a branch checkout in a new directory — never contains them.
`config.env` (ignored by `*.env`) and `plans/` (globally ignored) therefore
don't exist in new worktrees until copied. This is native git worktree
behavior, not a gtr bug.

gtr has built-in **smart file copying** for this: patterns declared via git
config are copied from the **main repo** into each new worktree at creation.
It's enabled **per repo** — project-specific, so there's no global default.

Enable for a repo (as done for linux_setup):

Copy file patterns:

```bash
git gtr config add gtr.copy.include "config.env"
```

Copy directories:

```bash
git gtr config add gtr.copy.includeDirs "plans"
```

- `gtr.copy.include` — glob patterns of files to copy (e.g. `**/.env.example`, `*.json`)
- `gtr.copy.includeDirs` — directories to copy (e.g. `node_modules`, `plans`)
- `gtr.copy.exclude` / `gtr.copy.excludeDirs` — patterns to skip (e.g. `**/.env.production`)
- Committed alternatives: `.gtrconfig` `[copy]` block (shareable);
  `.worktreeinclude` (gitignore-style, file patterns only)

Semantics: the copy is a **snapshot**, taken once at creation. Each branch
keeps its own independent `plans/`/`config.env` that may diverge. `git gtr new`
skips copying with `--no-copy`.

Re-sync an existing worktree from main (overwrites the target copies):

```bash
git gtr copy <branch>
```

Dry-run preview for all worktrees:

```bash
git gtr copy -a -n
```

Copy to all worktrees:

```bash
git gtr copy -a
```

Review active copy config:

```bash
git gtr config list
```

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
