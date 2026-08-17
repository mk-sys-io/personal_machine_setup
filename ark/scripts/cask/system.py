#!/usr/bin/python3
"""System cask operations — root password lifecycle and prereq checks.

Non-interactive infrastructure for ``ark enable``.  Calls the shared core
recipe (``cask_lib.cask_credentials_file``) with a root-password
``after_encrypt`` hook.

This module is a library, not a CLI entrypoint — it has no ``__main__`` block
and no argument parsing.  All preconditions (tle binary, network, openssl,
chpasswd, credential file) are the caller's responsibility; ark.py runs
``netmgr.guards.check_prereqs()`` once in Phase 1 and passes the resolved
``tle_bin`` path through to Phase 3's ``cask_credentials()`` call.
"""

from __future__ import annotations

import os
import subprocess

import opslog

from cask import CaskError, lib

# ── Root password lifecycle ─────────────────────────────────────────────────


def generate_root_password() -> str:
    r = subprocess.run(
        ["openssl", "rand", "-base64", "48"],
        capture_output=True, text=True, check=True, timeout=30
    )
    password = r.stdout.strip()
    if not password:
        raise CaskError("Failed to generate random password (openssl failed)")
    return password


def set_root_password(password: str) -> None:
    r = subprocess.run(
        ["chpasswd"],
        input=f"root:{password}",
        capture_output=True, text=True, timeout=10, check=False
    )
    if r.returncode != 0:
        raise CaskError(f"Failed to change root password: {r.stderr.strip()}")


def verify_root_password(_password: str) -> None:
    with open("/etc/shadow") as f:
        for line in f:
            if line.startswith("root:"):
                pw_hash = line.strip().split(":")[1]
                break
        else:
            raise CaskError(
                "Root account not found in /etc/shadow.\n" +
                "       This should never happen — system may be corrupt."
            )
    if not pw_hash or pw_hash in ("!", "*", "!*"):
        raise CaskError(
                "Root account is locked or has no password hash.\n" +
                "       Run 'passwd root' immediately to set a working password."
        )


def update_cred_file(path: str, password: str) -> None:
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path) as f:
            lines = f.readlines()
    lines = [line for line in lines if not line.startswith("root_password=")]
    lines.append(f"root_password={password}\n")
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.writelines(lines)
    os.rename(tmp, path)
    os.chown(path, lib.MIKE_UID, lib.MIKE_GID)


def _root_password_lifecycle() -> None:
    """Generate, set, and verify a new root password, then persist it."""
    password = generate_root_password()
    opslog.register_secret(password)
    set_root_password(password)
    verify_root_password(password)
    cred_path = os.path.join(lib.CASK_WORK_DIR, "system.credentials")
    update_cred_file(cred_path, password)


# ── System cask wrapper ─────────────────────────────────────────────────────


def cask_credentials(tle_bin: str, duration: str) -> None:
    """Cask system credentials with root-password lifecycle.

    Non-interactive thin wrapper over cask_lib.cask_credentials_file.
    No gates or prompts inside — the caller owns preconditions (run
    netmgr.guards.check_prereqs once at the boundary).
    """
    cred_path = os.path.join(lib.CASK_WORK_DIR, "system.credentials")
    cask_path = os.path.join(lib.CASK_DIR, "system.cask")
    lib.cask_credentials_file(
        tle_bin, cred_path, cask_path, duration,
        after_encrypt=_root_password_lifecycle,
    )
