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

```bash
git gtr rm feature/auth
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

### Worktrees & the Zed window

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
