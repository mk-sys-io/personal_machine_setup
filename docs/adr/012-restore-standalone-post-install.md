# Restore runs standalone post-install.py; trunk converges configs only

- Status: Accepted (2026-10-08) · Supersedes: 003 (trunk-wiring verdict; module split stands) · Source:
  restore-isolation decision 2026-10-08

## Decision

`lib/40-restore.py` is a standalone self-contained CLI, run only after
`./install.py` has converged, the reboot is taken, and the system
verified stable. `restore_home` leaves the install.py step table — no
flag, no skip logic. The lib assumes post-install.py state, prints that
assumption (stderr banner, non-blocking), and verifies preconditions
fail-loud without converging anything.

## Why

- System setup requires a reboot (NVIDIA/kernel/WiFi); landing livedata
  pre-reboot mixes config faults with data faults and risks collisions
  with reboot-pending state.
- Isolation makes restore intentional; the blank-iron gate (absent-or-empty
  or fail-stop) remains the data backstop.
- Rejected: `--restore` opt-in flag (absence already expresses it);
  `--only restore_home` as the path (a selection filter is not an entry
  point); lib converging its own preconditions (trunk's job).
- Revisit only if: a timer/operator flow needs trunk-driven restore again.

## Consequences

- Good: bare `./install.py` is restore-free by construction; unknown
  `--only`/`--skip restore_home` keys fail loud (`exit 2` naming valid keys).
- Bad (accepted cost): the password chain lives in the lib (file flag →
  `--password-command` → TTY ≤3 → paperkey hint); a new machine takes two
  artifacts instead of one.
