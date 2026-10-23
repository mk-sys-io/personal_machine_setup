#!/usr/bin/env python3
"""APT transaction flags (Python canonical for the lib/ Python modules).

MIRROR of the APT_* arrays in lib/helpers/common.sh -- keep the two in
lockstep, values must be byte-identical. There is no sh->py bridge in this
repo (no Python file sources common.sh); deliberate duplication is the
established convention, same as opslog.py / verify.py / 65-vm.py. Hoisting
the bash half into common.sh buys the four bash call sites one definition;
this module buys the Python call sites one definition.

Every hang this prevents is "something wants to ask a question and nobody is
there to answer it": a debconf dialog, a needrestart prompt, or apt blocking
on the dpkg lock forever. That last case is invisible by construction --
under a step, opslog.run() pipes stdout/stderr into the transcript, so apt's
"Waiting for cache lock" message lands in a file nobody is watching.

   APT_NONINTERACTIVE_ENV  debconf/needrestart take defaults, never draw
   APT_COMMON_OPTS         fail fast on the dpkg lock; bound every HTTP fetch
   APT_DPKG_OPTS           never stop on a modified conffile (apt syntax)
   APT_DPKG_FLAGS          same intent for bare `dpkg`, which has no -o
                           (no current call sites; retained for re-use)

ASYMMETRY, deliberate -- do not "fix": APT_NONINTERACTIVE_ENV carries "env"
as argv[0] because Python has no shell to supply it, whereas the bash array
holds only KEY=VAL pairs and each call site writes `env` literally.

Usage:

    apt-get:  ["sudo", *APT_NONINTERACTIVE_ENV, "apt-get", "install", "-y",
               *APT_COMMON_OPTS, *APT_DPKG_OPTS, *pkgs]
    deb:      ["sudo", *APT_NONINTERACTIVE_ENV, "apt-get", "install", "-y",
               *APT_COMMON_OPTS, *APT_DPKG_OPTS, str(deb)]

Stdlib only. Import-safe: importing never runs anything.
"""
from __future__ import annotations

APT_NONINTERACTIVE_ENV = [
    "env",
    "DEBIAN_FRONTEND=noninteractive",
    "NEEDRESTART_MODE=a",
]
APT_COMMON_OPTS = [
    "-o",
    "DPkg::Lock::Timeout=120",
    "-o",
    "Acquire::Retries=3",
    "-o",
    "Acquire::http::Timeout=30",
    "-o",
    "Acquire::https::Timeout=30",
]
APT_DPKG_OPTS = [
    "-o",
    "Dpkg::Options::=--force-confdef",
    "-o",
    "Dpkg::Options::=--force-confold",
]
# Bare `dpkg` has no -o; --force-* is its own flag syntax.
APT_DPKG_FLAGS = ["--force-confdef", "--force-confold"]
