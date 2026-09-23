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

The hook pins `-c ~/.config/gitleaks/gitleaks.toml` (machine-wide shared
config, deployed from the repo `.gitleaks.toml` by the same script), so one
versioned policy covers every repo — including the gopass secret store,
whose `.gpg` blobs are allowlisted there. Per-repo `.gitleaks.toml` files
are inert on this machine (shared config outranks them); they remain as the
portable story for other machines. `GITLEAKS_CONFIG` at the same path is
exported by `dotfiles/bashrc` for hand-run invocations.

Gitleaks must be installed (see `packages/apt.txt`). No per-repo hook config needed —
the global hooksPath covers all repos automatically.

### `gitleaks.toml` (template)
Per-repo gitleaks config template for scaffolding new projects (see
`tools/init.sh`). Gitleaks is a secret scanner that prevents committing
credentials, tokens, and keys. The template extends gitleaks' 160+ default rules
and adds allowlists for common false positives (template files, example configs,
GPG-encrypted blobs). Kept in sync with the repo `.gitleaks.toml` and the
embedded copy in `tools/init.sh` (which `make dev` refreshes by injection —
never hand-edit the injected block).

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

Git worktree manager for running OpenCode panels per branch.
Each panel gets its own isolated directory and branch without duplicating
the repository. Prevents file conflicts between concurrent AI agents.

**Installed via:** `lib/50-github_setup.sh` (clones to `~/.local/share/gtr`, runs gtr's own `install.sh`)

**Alias:** `g` function in `dotfiles/bashrc` — with a branch name it creates a
worktree (forked from your current branch) and switches the current Zed window
to it (via `zed -r`). It also forwards other gtr verbs, so
`g rm <branch>`, `g list`, and `g cd` work too. Shell integration adds tab
completion and `gtr cd` interactive worktree picker.

### Quick start

Verify global config (done by lib/50-github_setup.sh):

```bash
git gtr config list --global
```

Create worktree + switch the Zed window (from g alias):

```bash
g feature/auth
```

OpenCode is started manually: press `Ctrl+`` (new terminal) in the switched
window — it starts in the worktree root — then run `opencode`.

### Base branch behavior

By default, `git gtr new <branch>` forks from the **remote** default branch
(`origin/<defaultBranch>`), i.e. the last commit pushed to remote. If your local
`main` has unpushed commits, new worktrees will start from an older base.

The `g` alias passes `--from-current`, which forks from your current local
branch instead. Use `--from <ref>` for an explicit base.

From a tag:

```bash
git gtr new feature/x --from v1.2.3
```

From local main:

```bash
git gtr new feature/x --from main
```

Override for a specific repo — change into it:

```bash
cd ~/special-repo
```

Then set a different AI tool:

```bash
git gtr config set gtr.ai.default pi
```

See all worktrees:

```bash
git gtr list
```

Clean up when done — worktree only (branch preserved):

Remove worktree only (branch preserved):

```bash
git gtr rm feature/auth
```

Remove worktree and branch:

```bash
git gtr rm feature/auth --delete-branch
```

Or worktree + branch:

```bash
git gtr rm feature/auth --delete-branch
```

### Shell integration

Added to `dotfiles/bashrc` — deployed to `~/.bashrc` via Makefile. Provides
tab completion and `gtr cd` interactive worktree picker.

See [gtr docs](https://github.com/coderabbitai/git-worktree-runner) for full
configuration reference.

### Copying ignored files into worktrees

Gitignored/untracked files aren't part of any commit, so a freshly created
worktree — a branch checkout in a new directory — never contains them.
A gitignored `*.env` secret (the pre-12-C `config.env`/`dev/github.env`
schema lived here) and `plans/` (globally ignored) therefore
don't exist in new worktrees until copied. This is native git worktree
behavior, not a gtr bug. (`config.txt` itself is committed and syncs
normally.)

gtr has built-in **smart file copying** for this: patterns declared via git
config are copied from the **main repo** into each new worktree at creation.
It's enabled **per repo** — project-specific, so there's no global default.

Enable for a repo (as done for linux_setup):

Copy file patterns:

```bash
git gtr config add gtr.copy.include "config.txt"
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
keeps its own independent `plans/` snapshot that may diverge. `git gtr new`
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

`g <branch>` switches the current Zed window to the worktree via `zed -r`,
which replaces the current window's workspace. This matters because Zed's Git
panel only reflects the project root — it doesn't show per-branch status for
other worktrees.

`zed -r` is a hard workspace replacement: Zed tears down the previous
workspace's terminal panel (kills the shell), so OpenCode cannot be launched
from the same terminal. It is started manually afterwards — a new terminal
(`Ctrl+``) in the switched window opens at the worktree root by default
(`terminal.working_directory: current_project_directory`), then run `opencode`.

### `g cd` vs `g <branch>`

`g cd` opens an interactive worktree picker. **Enter** switches git only (the
shell's working directory). **ctrl-e** also switches the current Zed window
(runs `git gtr editor`, configured as `zed -r`).

For one-command both — git and the Zed window — use **`g <branch>`**.

### Switching between worktrees

Git enforces single-checkout: a branch can only be checked out in one worktree,
so `git switch` / `git checkout` / Zed's branch picker fail with:

```
fatal: '<branch>' is already used by worktree at '...'
```

`g <branch>` switches the current Zed window to that worktree. To switch
without creating anything new, use `g <existing-branch>` (it opens the existing
worktree) or Zed's `git:worktree` picker (`alt-ctrl-shift-w`) to switch between
all worktrees. Outside Zed, use `git gtr go <branch>` / `gtr cd` to navigate.
