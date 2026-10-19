# Pi Agent Removal Postmortem

> **Note:** the deleted files' full content lives in git history — nothing was
> destroyed, only removed from the working tree (see [Restorability](#restorability)).

The Pi coding agent integration — extension, settings seed, provisioning CLI,
shared provider registry, and the npm binary — was removed from this repository
and from the live workstation. It never became operational: provisioning was
gated on gopass, which never installed (GitHub rate-limit skip, install log
2026-10-16), so no credentials, no `providers.json`, and no curated allowlists
ever existed, and the deployed extension registered **zero providers**.

## Old components

| Component | Source | Live target | Deployed by |
|---|---|---|---|
| Live extension | `dev/pi/deploy/extensions/live/index.ts` | `~/.pi/agent/extensions/live/index.ts` | `make dev` (verbatim `cp --parents`) |
| Settings seed | `dev/pi/deploy/settings.seed.json` | `~/.pi/agent/settings.json` (only-if-absent) | `make dev` |
| Provisioning CLI | `tools/pi_setup/` (12 modules) | `~/.local/bin/pi-setup` (zipapp) | `make tools` |
| Provider registry | `tools/provider_registry/` (5 modules) | python user site-packages snapshot | `make tools` |
| Agent binary | `packages/npm_packages.txt:8` | `/usr/bin/pi` (`@earendil-works/pi-coding-agent`, npm global) | `lib/20-packages.py` |
| Docs | `dev/pi/docs/PI.md`, `dev/pi/README.md` | — (never deployed) | n/a |

## Provisioning flow as designed

gopass store `provider-registry/<id>` (field `key`) → `pi-setup auth` →
`~/.pi/agent/auth.json` (atomic, 0600) → `pi-setup probe|fetch --write` →
`extensions/live/curated/*.json` + `providers.json` → the extension registers
the five providers (OpenRouter, NaraRouter, OpenCode Zen, NVIDIA NIM, Gemini)
and filters catalogs through the curated allowlists. Default model:
`opencode/big-pickle`.

## Live state at removal

Nothing of value was lost. `~/.pi/agent/settings.json` and the deployed
`index.ts` were byte-identical to their repo sources; `auth.json` and
`models-store.json` were empty `{}` (0600, created as a probe side-effect);
`providers.json` and `curated/` were never generated; the gopass binary and
store never existed. The zipapp and site-packages copy are regenerable from
source; the npm binary is regenerable from the package list.

## Restorability

**All deleted content is preserved in git history** — every file deleted by
this change exists byte-for-byte at the pre-deletion commit `c382f07` (and was
already pushed at `ea5de52`): all 24 tracked files (7 in `dev/pi/`, 12 in
`tools/pi_setup/`, 5 in `tools/provider_registry/`) plus the npm list line.
Nothing was destroyed; only the working tree copy was removed. Recover a single
file or the whole tree from any clone:

    git show c382f07:dev/pi/docs/PI.md                      # one file
    git checkout c382f07 -- dev/pi/ tools/pi_setup/         # restore paths

Caveats: the repo is excluded from restic (`etc/restic/excludes.txt`), so
history survives only on this disk and GitHub; restoration must cite a
**pre-deletion** commit (`c382f07`/`ea5de52`), never a post-deletion one;
live-only artifacts are not in git but are regenerable or empty as above.

## Cleanup performed

Repo: deleted the three directories and the npm list line; removed the Pi
blocks in `Makefile`, the `pi-setup` checks in `tools/verify.py`, the sweep and
owned-list entries in `lib/helpers/clean_stale.sh`, the `~/.pi/agent`
precondition and converge step in `lib/40-restore.py`, and pi references in
`AGENTS.md`, `docs/gopass.md` (Pattern 2 example), `docs/provider-evaluation.md`
(link), `docs/backlog.md` (phantom path), espanso, and two comments.
`docs/adr/005` body untouched per ADR house rule (history, not rewritten).
Live: `~/.pi/` removed, `pi-setup` zipapp and site-packages copy removed,
npm global uninstalled.

## Outcome

Removed cleanly, **restorable in full** from a pre-deletion commit — the
content of every deleted file remains in git history. The provisioning chain
had never run — no credentials, no curated files, zero registered providers —
so the removal costs nothing operationally. The design (gopass-sourced keys,
curated free-model allowlists, manifest-driven extension) remains documented
here and in git history should it be revived.
