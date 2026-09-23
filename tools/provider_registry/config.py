"""Static configuration: env-driven paths, timing bounds, gopass accessor."""
from __future__ import annotations

import os
import shutil
import subprocess

from .errors import ToolError

# gopass entry prefix for provider API keys (structured secret, key field).
# Entry layout: <prefix>/<provider-id> with key + strategy fields.
GOPASS_ENTRY_PREFIX = os.environ.get("PI_GOPASS_PREFIX", "provider-registry")
# gopass binary name/path (overridable for dev/test).
GOPASS_BIN = os.environ.get("PI_GOPASS_BIN", "gopass")

# NOTE (Phase 12-B): this accessor is scoped to provider_registry and
# pi_setup. Shell infra under lib/ reads secrets via the canonical
# executable lib/gopass.sh, not via this function. New consumers: shell
# executes the CLI, Python imports here. Do not wrap one in the other
# without a Phase-12 spec amendment.


def gopass_show_field(entry: str, field: str) -> str | None:
    """Read a structured field from a gopass entry (FM1-FM7 exit-code mapping).

    Returns the field value, or None when the entry/field is missing (FM3
    exit 10 — benign). Hard errors raise ToolError with gopass stderr plus a
    human-readable fix. FM7: no --yes/-n flags and stdin is inherited, so a
    first-use pinentry prompt surfaces normally.
    """
    if shutil.which(GOPASS_BIN) is None:
        raise ToolError(
            "gopass not found — run install.sh (lib/20-packages.sh installs it)"
        )
    proc = subprocess.run(
        [GOPASS_BIN, "show", entry, field],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout.strip() or None
    if proc.returncode == 10:
        return None
    if proc.returncode == 6:
        raise ToolError("gopass store not initialized — run: gopass setup")
    if proc.returncode in (11, 18):
        raise ToolError(
            f"gopass show {entry} {field} failed (exit {proc.returncode}):"
            f" {proc.stderr.strip()}"
        )
    raise ToolError(
        f"gopass show {entry} {field} failed (exit {proc.returncode}):"
        f" {proc.stderr.strip()}"
    )
