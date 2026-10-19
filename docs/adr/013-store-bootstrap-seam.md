# Vault-secrets bootstrap is trunk-wired; livedata stays standalone

- Status: Accepted (2026-10-09) · Amended 2026-10-19 (step renamed
  40-restore-vault; policy moved into lib/40-restore.py) · Exception to: 012
  (isolation verdict stands for livedata) · Source: first-run secret gap
  (50-github hard-requires the store token)

## Decision

`install.py` gains one trunk step, `21-store-bootstrap`, running 3rd
(after `20-packages`, before `22-wifi-migrate`). It restores only the
`--tag vault` stream (gopass store + GPG key-export import) via
`lib/40-restore.py`, idempotent (`SKIP` when the store already
unlocks), abort-grade (`FAIL` stops the run). Full livedata restore
stays standalone post-install per 012.

## Why

- `50-github_setup.sh` hard-fails without `services/github` from the
  store, so a restore-free first run can never go green; `20-packages`
  is token-tolerant (unauthenticated degrade), so exactly one step runs
  pre-secrets.
- restic + gopass both ship via `20-packages` (40-A / github_deb), so
  the seam must sit mid-trunk — pre-install restore is impossible.
- Rejected: hoisting restic/gopass into `ensure_stage0` — blast radius
  on the fail-fast gate for zero UX gain; full pre-reboot livedata
  restore — mixes config faults with data faults (012 rationale stands).
- Revisit only if: a secret-needing step must run before `20-packages`.

## Consequences

- Good: first `./install.py` goes green end-to-end; no step downstream
  of the seam ever observes a missing store.
- Bad (accepted cost): trunk now owns one restore call (vault-tag
  only); `--skip 21-store-bootstrap` re-exposes the old loud 50-github
  failure by operator choice.

## Amendment (2026-10-19)

The wrapper `lib/21-store-bootstrap.py` is deleted. `install.py`'s step is
now `40-restore-vault`, pointing at `lib/40-restore.py` with
`args=["--tag","vault"]`. Agent posture, the gopass/pinentry gates,
idempotency, the reboot exemption and the RESULT trailer now live in
`40-restore.py`. `--skip 21-store-bootstrap` is now
`--skip 40-restore-vault`. The verdict is unchanged: trunk-wired vault,
standalone livedata.
