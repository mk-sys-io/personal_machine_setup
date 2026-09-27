# Makefile stays; harden clean-stale, defer managers

- Status: Accepted (2026-09-28) · Source:
  `plans/decisions/dotfile-runner-2026-09.md`

## Decision

Dotfiles and dev deploy stay on the `Makefile` runner. This closes
the runner question in
`plans/archive/system/why-not-ansible-dotfile-managers.md` and
`repo-rework.md` Phase 3. The stale-file
problem is runner-independent, so the fix is hardening `make
clean-stale` (all managed dirs, dry-run, max-delete abort, `comm`
correctness — spec in `30-dotfiles-makefile.md`), not a new runner.
Manager tools are deferred indefinitely, not adopted.

## Why

Just charges a full `$$`/quoting/dotenv audit (trixie ships 1.40 vs
upstream 1.58) plus bootstrap churn across ~15 live files, for arg
passing and `--list` — no new capability. Stow symlinks into
`/etc`/`/opt` are a security anti-pattern; chezmoi runs as user and
cannot reach root paths.
`Rejected: Just migration — audit plus churn, no new capability;
hybrid shim — two runners, one problem; chezmoi/Stow — cannot do
/etc+/opt safely; yadm/Ansible — fleet-tool mismatch;
gomplate-for-dotfiles — see 009.`
`Revisit only if: a second machine appears or hardened clean-stale
still bites.`

## Consequences

- Good: zero-dep `make dotfiles` keeps fresh-clone/offline working;
  one runner, one mental model; dead `@USERNAME@` and
  `@OBSIDIAN_VAULT_PATH@` substitutions removed.
- Bad (accepted cost): no `--list`/arg ergonomics; `clean-stale`
  hardening is owed work, not done work.
