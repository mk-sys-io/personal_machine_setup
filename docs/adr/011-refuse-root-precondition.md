# User-scoped entry points refuse root; privilege only via internal sudo

- Status: Accepted (2026-09-28)

## Decision

Every user-scoped entry point aborts when euid is 0: Python modules
via shared `lib/helpers/preconditions.py::require_user()`
(`install.py`, `20-packages.py`, `25-searxng.py`, `45-brave.py`);
all bash modules via the refuse-root branch in `lib/helpers/common.sh`
(which every module sources — no per-module guard). Privileged work
keeps its own internal `sudo` calls; the guard and internal sudo
compose. Named exceptions: `lib/65-vm.py` requires root (loop, mount,
mkfs, chroot under `/var/lib/libvirt`), `lib/60-ark.sh`'s "runs as
root" header meant escalates-internally (clarified). `REAL_HOME`'s
`SUDO_USER` branch is retired as unreachable.

## Why

A root process resolving a user's home silently produces root-owned
user state (precedent: 25-searxng's `real_home()` adaptation wrote a
root-owned venv, settings, unit — then the venv-present guard skipped
every re-run, persisting the damage behind an OK), reaches the wrong
gopass store, and talks to the wrong `systemctl --user` bus. `su`
and `sudo` are indistinguishable by euid, so one guard covers both;
`SUDO_USER` informs only the message hint.
`Rejected: adapt-and-drop-privileges — unimplementable under pure
su, doubles privilege code paths, trusts SUDO_USER; per-module bash
copies — dead code, the source-time assert fires first; generic
utilities.py home — junk-drawer anti-pattern, named cohesive module
instead.`
`Revisit only if: a module gains a legitimate must-run-as-root mode.`

## Consequences

- Good: one invocation contract per runtime, greppable (`require_user`
  / refuse-root branch); root misuse fails loud before any work.
- Bad (accepted cost): `55-security.sh` standalone-as-root no longer
  tolerated (it never worked — the USERNAME assert already refused it,
  now with a clear message).
