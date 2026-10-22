# install.py unlocks root to DISABLE_ROOT_PASSWORD for `su -` recovery

- Status: Accepted (2026-10-22) · Source: docs/recovery/bootstrap-wifi.md

## Decision

`install.py` runs `lib/57-root-account.sh`, which sets root's password to
the committed `DISABLE_ROOT_PASSWORD` when root is locked (d-i's
blank-password state) and skips when a usable hash already exists. It uses
`chpasswd -c YESCRYPT` with an `openssl passwd -6` / `usermod -p` fallback,
and never clobbers an Ark-casked or operator-set password.

## Why

The bare-iron setup deliberately leaves root locked so d-i installs sudo
and adds the user to the sudo group. The side effect is no `su -` or
single-user root entry: every password is rejected until something sets a
hash. `install.py` already owns post-install convergence, and `ark disable`
already resets root to the same value, so reusing it restores a known
recovery credential without a second secret.
Rejected: set a root password in d-i — sudo is then not installed
(reinstall). Rejected: random generated password — no recovery value.
Rejected: leave root locked — no console/single-user entry.
Revisit only if: Ark's cask model needs root to stay locked between runs.

## Consequences

Good: `su -` and single-user recovery work on every converged machine; the
value matches `ark disable`; idempotent (SKIP when already usable).
Bad (accepted cost): `DISABLE_ROOT_PASSWORD` is a weak 4-digit value, so
enabling `su -` widens the local brute-force surface; the no-clobber guard
is policy, not a permission barrier — root can always rewrite
`/etc/shadow`.
