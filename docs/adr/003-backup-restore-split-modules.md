# Split backup/restore modules; trunk only restores

- Status: Superseded (2026-10-08) by 012 — trunk-wiring verdict only;
  module split stands · Supersedes: — · Source:
  module-split decision 2026-09-27

## Decision

`lib/40-restore.sh` (trunk-wired, restore-only), `lib/40-backup.sh`
(standalone, runnable by hand, claimed later by the timer), and
`lib/helpers/restic_common.*` shared plumbing. Trunk only ever
restores; backup never fires in trunk. No new install.py flags —
`--only`/`--skip` covers opt-out (`--skip restore_home`).

## Why

- Restore and backup have opposite safety postures (converge-to-repo
  vs push-livedata-out); one module can't hold both defaults sanely.
- Overwrite policy: `--overwrite if-changed` default (additive only,
  no `--delete` first pass); backup-before-restore tagged
  `pre-restore-<ts>`; destructive converge gated by `RESTORE_DELETE=1`;
  no trunk prompts (non-interactive safe).
- Revisit only if: the timer leg (parked in `future/`) forces a shared
  scheduling interface.

## Consequences

- Good: trunk stays read-only-safe; the backup script is testable by
  hand today and timer-claimable tomorrow without refactor.
- Bad (accepted cost): three files plus a contract instead of one —
  `restic_common.*` drift is the thing to watch.

## Amendment (2026-09-28)

Language trial resolved the implementation to Python:
`lib/40-restore.py`, `lib/40-backup.py`,
`lib/helpers/restic_common.py` (C1+C2+C4 convert rule; `install.py`
dispatches `.py` natively). Decision, firing rule, and overwrite
policy unchanged.
