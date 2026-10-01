# git/ — Git workflow automation

Configuration and tools for git workflow: global ignore rules, global hooks, per-repo secret scanning.

## Files

### `ignore`
Global gitignore — excludes `plans/` from all repos so plan files aren't committed.
Deployed to `~/.config/git/ignore` by `make dev`.

See [plan-workflow.md](../opencode/docs/plan-workflow.md) for the dual-ignore design
(`.ignore` for ripgrep inclusion, global gitignore for git exclusion).

### `pre-commit`
Global git pre-commit hook — runs `gitleaks protect --staged` on every commit.
Deployed to `~/.git-hooks/pre-commit` by `make dev` with `core.hooksPath`
configured globally. The deploy is skipped when the `gitleaks` binary is
absent: a hook that cannot run would block every commit.

The hook pins `-c ~/.config/gitleaks/gitleaks.toml` (the machine-wide shared
config, a copy of `gitleaks.toml` from this directory), so one versioned
policy covers every repo — including the gopass secret store, whose `.gpg`
blobs are allowlisted there. `GITLEAKS_CONFIG` at the same path is exported by
`dotfiles/bashrc` for hand-run invocations.

Per-repo `.gitleaks.toml` files are therefore inert *for commits* (the hook's
`-c` outranks them). They are still read when you invoke gitleaks yourself in
that repo — which is why `init` writes an extender rather than a copy.

Gitleaks must be installed (see `packages/apt.txt`). No per-repo hook config needed —
the global hooksPath covers all repos automatically.

### `gitleaks.toml` (the deployed policy)

**This file is the source of truth for gitleaks config in this repo.** It
extends gitleaks' 160+ default rules and adds allowlists for common false
positives (template files, example configs, GPG-encrypted blobs). Gitleaks is
a secret scanner that prevents committing credentials, tokens, and keys.

`make dev` deploys it to two places:

| Live path | Consumer |
| --- | --- |
| `~/.config/gitleaks/gitleaks.toml` | machine-wide — pinned by the pre-commit hook's `-c` and by `GITLEAKS_CONFIG` |
| `~/.config/init/gitleaks.toml` | `~/.local/bin/init` — what per-repo extenders point at |

There is **no injected or embedded copy** in `tools/init.sh` any more; `init`
discovers the live file at runtime and writes a thin extender.

The repo-root `.gitleaks.toml` is itself such an extender, scaffolded by
running `init` here. It holds **no policy** — only a `title`, an `[extend] path`
back to this file, and an empty `[allowlist]` — so it cannot drift from this
template: there is nothing in it to fall out of sync. It exists for non-interactive
runs (cron, scripts, tooling that doesn't source `bashrc`) where neither `-c` nor
`GITLEAKS_CONFIG` is set; in an interactive shell both outrank it. Because it
points at the checkout rather than a deployed copy, edits to this file take effect
on the next scan with no `make dev` in between.

To re-scaffold it: `rm .gitleaks.toml && ./tools/init.sh --yes`.

#### Merge semantics (verified on the installed gitleaks 8.16.0)

- `[extend] path` chains configs to a **depth of 2**.
- `useDefault` and `path` are **mutually exclusive** — pick one.
- Allowlist arrays **append** across the chain (duplicates permitted).
- Duplicate rule `id`s **override** — the child's rule wins.

That last one is the sharp edge: an extender cannot *remove* rules by
`disabledRules` (that key is unrecognised in 8.16.0 and silently ignored), but
it **can** neutralise an inherited rule by re-declaring its `id`. So extenders
must stay add-only **by convention** — gitleaks does not enforce it. `init`
only ever seeds an empty `[allowlist]`, so its output is additive by
construction.

## Setup on a new project

```bash
tools/init.sh
```

Run from the target repo root. This scaffolds:
- `plans/` — opencode plan files (git-excluded, ripgrep-visible)
- `.ignore` — `!plans/` re-include for `@plans/` references
- `.gitleaks.toml` — a thin **extender** with `title` set to your repo name

The extender's `path` is whichever policy copy `init` finds first: the
checkout's `dev/git/gitleaks.toml` when you run `tools/init.sh` from this
repo, otherwise the deployed `~/.config/init/gitleaks.toml` (which is what
the installed `~/.local/bin/init` uses on any other machine).

To customize gitleaks rules after setup, edit `.gitleaks.toml` — add per-path
allowlists for test fixtures, docs, or custom token patterns under its
`[allowlist]`. Do **not** re-declare a rule `id` inherited from the shared
policy; that silently overrides it.

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
