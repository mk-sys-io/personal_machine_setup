"""Subprocess helpers shared by netmgr modules.

Absorbs run()/run_or_warn() from ark.py (see
plans/ark-rework/networking-architecture-rework.md). Gomplate-templated:
the constant is quoted and int()-wrapped so the raw source stays valid
Python (parity with the cask_lib.py int-token pattern).
"""
from __future__ import annotations

import subprocess

import opslog

SUBPROCESS_TIMEOUT = int("{{ .Env.SUBPROCESS_TIMEOUT }}")


def run(
    cmd: list[str],
    *,
    timeout: int | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command with logging and structured error handling."""
    effective_timeout = timeout or SUBPROCESS_TIMEOUT
    opslog.debug("exec: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=capture, text=True, timeout=effective_timeout
    )
    if check and result.returncode != 0:
        opslog.error(
            "command failed (exit %d): %s\nstderr: %s",
            result.returncode,
            cmd,
            result.stderr,
        )
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def run_or_warn(cmd: list[str], *, timeout: int | None = None) -> bool:
    """Run a command, warn on failure instead of raising. Returns success bool."""
    try:
        run(cmd, timeout=timeout, check=False)
        return True
    except (subprocess.SubprocessError, OSError) as e:
        opslog.warn("command failed (non-fatal): %s — %s", cmd, e)
        return False
