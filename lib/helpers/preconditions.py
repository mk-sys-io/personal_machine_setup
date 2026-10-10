#!/usr/bin/env python3
"""Privilege precondition shared by user-scoped entry points.

Single implementation of the refuse-root guard (see ADR-011). Every
user-scoped entry point — install.py and all lib/ modules except the
named machine-scoped ones — calls require_user() first and aborts when
euid is 0. Bash modules reach the same implementation via
`--check` (thin require_user() delegate in lib/helpers/common.sh).

Why refuse instead of adapt (one pattern, no SUDO_USER/REAL_HOME
branching): a root process that resolves a user's home silently
produces root-owned state in that home (the 25-searxng precedent:
root-owned venv, settings.yml, limiter.toml, systemd unit — with the
venv-present idempotency guard then skipping every re-run, so the
damage persisted behind an OK). Root also reaches the wrong gopass
store (GitHub installs degrade to unauthenticated rate limits) and the
wrong `systemctl --user` bus. Refusal converts all of that into one
loud message.

su vs sudo is indistinguishable here and needs no handling:
os.geteuid() == 0 in both (sudo does not preserve user identity at the
kernel level). The SUDO_USER hint in the message is cosmetic only —
it diagnoses how you likely got root, never a security decision.

Two axes, not one dispute: this guard answers *who may invoke* the
entry point. Internal `sudo` calls answer *how privileged ops
escalate* — modules that do privileged work (apt, dpkg, /etc,
systemctl) keep them; require_user() and internal sudo compose
(20-packages.py carries both).

Never callers (opposite polarity, by design): lib/65-vm.py
(requires root — loop/mount/mkfs/chroot under /var/lib/libvirt).
It refuses non-root instead. lib/60-ark-deploy.py and
lib/61-ark-policy.py run as user and escalate via internal sudo.

Stdlib only. Import-safe: importing never refuses, exits, or prints.
"""
from __future__ import annotations

import argparse
import os
import sys


def require_user(prog: str, usage: str) -> bool:
    """Return True when safe to proceed; refuse root with redirect.

    As root (euid 0, however reached) prints the redirect to stderr
    and returns False. Otherwise returns True silently.
    """
    if os.geteuid() != 0:
        return True
    if os.environ.get("SUDO_USER"):
        hint = "looks like you prefixed with sudo — re-run without it"
    else:
        hint = "you are in a root shell — exit to your user and re-run"
    print(f"  ERROR: Do not run {prog} as root ({hint}).", file=sys.stderr)
    print(f"  Run it as your normal user: {usage}", file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    """CLI for bash callers: preconditions.py --check PROG USAGE."""
    parser = argparse.ArgumentParser(description="Refuse-root precondition check")
    parser.add_argument("--check", nargs=2, metavar=("PROG", "USAGE"), required=True)
    args = parser.parse_args(argv)
    prog, usage = args.check
    return 0 if require_user(prog, usage) else 1


if __name__ == "__main__":
    sys.exit(main())
