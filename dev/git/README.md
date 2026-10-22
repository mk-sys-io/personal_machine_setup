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
policy covers every repo. Legacy `.gpg` blobs (opaque ciphertext, e.g. old
encrypted stores) are allowlisted there. `GITLEAKS_CONFIG` at the same path
is exported by `dotfiles/bashrc` for hand-run invocations.

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
running `init` here. It holds **no policy** — only a `title` and an `[extend] path`
back to this file — so it cannot drift from this
template: there is nothing in it to fall out of sync. It exists for non-interactive
runs (cron, scripts, tooling that doesn't source `bashrc`) where neither `-c` nor
`GITLEAKS_CONFIG` is set; in an interactive shell both outrank it. Because it
points at the checkout rather than a deployed copy, edits to this file take effect
on the next scan with no `make dev` in between.

To re-scaffold it: `rm .gitleaks.toml && ./tools/init.sh --yes`.

#### Merge semantics (verified on upstream gitleaks 8.30.1, pinned in `packages/github_binary.txt`)

- `[extend] path` chains configs to a **depth of 2**.
- `useDefault` and `path` are **mutually exclusive** — pick one.
- Allowlist arrays **append** across the chain (duplicates permitted).
- Duplicate rule `id`s **override** — the child's rule wins.

Two sharp edges, both verified live on 8.30.1: an extender can neutralise an
inherited rule by re-declaring its `id`, **and** by naming it in `disabledRules`
(retested 2026-10-01 — the old "silently ignored" note described 8.16.0 and no
longer holds). So extenders must stay add-only **by
convention** — gitleaks does not enforce it. `init` seeds neither rules nor
allowlists, so its output is additive by construction.

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

Deprecated — gtr has been completely removed from this repo and system.
