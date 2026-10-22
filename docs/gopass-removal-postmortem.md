# gopass/GPG Removal Postmortem

> **Note:** the deleted files' full content lives in git history — nothing was
> destroyed, only removed from the working tree (see [Restorability](#restorability)).

gopass and the entire GPG stack were removed from this repository. The
gopass password store, its vault restic stream, the GPG key-export
machinery, and every consumer (`50-github_setup.sh`, `20-packages.py`)
are gone; there is deliberately **no in-repo replacement store**.
Bitwarden (external, phone-held) is the sole secret custody, and
tools read secrets via flags/env first or a `/dev/tty` prompt.

## Why removed

- **GPG pinentry tax.** Every restic operation could block on
  pinentry; the agent-posture machinery (`gpg-agent.conf`,
  `GPG_TTY`, `pinentry-curses`) existed only to make that bearable.
- **Vault stream was gutted and unexercised.** `etc/restic/excludes.txt`'s
  `$HOME/.*` deny matched the parent of both vault allowlist paths, so
  `--tag vault` snapshots were empty — restore "succeeded" and restored
  nothing. Backlog 013 (vault-restore-unverified) confirmed gopass was
  genuinely absent on the live machine: the whole stream had never run
  for real.
- **Two-store split.** Bitwarden for website credentials + gopass for
  API keys duplicated custody, backup, and bootstrap burden for zero
  operational gain once the store went unexercised.
- **50-github cascade.** The GitHub token reader hard-depended on a
  store that never materialized, degrading installs silently to
  anonymous rate limits.
- **ADR-013 seam complexity.** The store-bootstrap wrapper step,
  `--tag vault` role, `21`/`store`/`vault` aliases, and their
  preconditions were pure vault-stream scaffolding.

## What was removed (this change)

| Area | Removed |
|---|---|
| Helper | `lib/helpers/gopass.sh` (canonical secret reader CLI) |
| Backup | vault stream in `lib/40-backup.py` (`VAULT_PATHS`, gate, tag branches) |
| Restore | vault/GPG machinery in `lib/40-restore.py` (agent posture, key import, store gates, `--tag vault` role) |
| Trunk | `40-restore-vault` step + aliases in `install.py`; `gnupg`/`gpg` from stage-0 |
| Consumers | gopass branch of `github_token()` in `20-packages.py`; gopass token fetch in `50-github_setup.sh` |
| Packages | `gopass`, `gopass-archive-keyring`, `gnupg`, `pinentry-curses` from `packages/apt.txt`; gopass apt repo; `gnupg` from both stage-0 lists |
| Config | `GPG_TTY` block in `dotfiles/bashrc`; `!$HOME/.gnupg` re-include in `excludes.txt`; gopass custody comments in `config.txt` and helpers |
| Docs/ADRs | `docs/gopass.md`, `docs/adr/013` deleted; `002`/`005` superseded; runbooks rewritten |

## What replaced it

- **Secret chain** (`lib/helpers/restic_common.py`): explicit
  flag/env first, else `/dev/tty` prompt (`prompt_tty`: IEXTEN-clear,
  paste-scrub, `accepted (N chars)`, empty-flow retype) — symmetric on
  backup and restore; headless without flags/env fails naming them.
- **B2 credentials**: ambient `AWS_*` pair, else per-field `/dev/tty`
  prompts (Bitwarden-held).
- **50-github**: `GITHUB_TOKEN` env or `/dev/tty` prompt; empty or
  declined → warn + continue unauthenticated (exit 0).
- **20-packages**: anonymous GitHub API only (60 req/h, enough for the
  installer's few lookups).

## Considered alternatives (not adopted)

| Alternative | Verdict | Why deferred/rejected |
|---|---|---|
| Bitwarden CLI (`bw`) | GO (dep-vet) | Unadopted "for now" — TTY prompts suffice; revisit if prompt friction bites |
| SOPS + age | GO (dep-vet) | Encrypted-file workflow is repo-shaped; no current in-repo secret files need it |
| gopass + age (drop GPG only) | — | Keeps gopass itself, the primary removal target |
| 1Password / Doppler / Infisical / Vault / OpenBao | — | Paid tiers or network servers; wrong shape for an offline-first single operator |
| OS Secret Service (libsecret/oo7) | — | Violates Option C (zero secrets in the keyring) |
| passage / rage / himitsubako | CAUTION/NO-GO (dep-vet) | Stale or single-maintainer dormancy |

## Restorability

**All deleted content is preserved in git history** — every file deleted
by this change exists byte-for-byte at the pre-deletion commit
`57793c1`: `lib/helpers/gopass.sh`, `docs/gopass.md`,
`docs/adr/013-store-bootstrap-seam.md`, plus every pre-edit version of
the rewritten files. Nothing was destroyed; only the working tree copy
was removed. Recover a single file or the whole tree from any clone:

    git show 57793c1:docs/gopass.md                      # one file
    git checkout 57793c1 -- lib/helpers/gopass.sh        # restore paths

Caveats: the repo is excluded from restic (`etc/restic/excludes.txt`),
so history survives only on this disk and GitHub; restoration must cite
a **pre-deletion** commit (`57793c1`), never a post-deletion one.

## Scope and outcome

Repo-scoped: the live workstation may still carry `gopass`/`gnupg`
packages and `~/.local/share/gopass` until manually purged (out of
scope here; nothing in the repo reads them anymore). The ADR-002
GPG-export chain and ADR-005 sole-store decision are marked superseded;
backlog 013 is retired. The design remains restorable in full from
`57793c1` should store-backed custody ever be revived.
