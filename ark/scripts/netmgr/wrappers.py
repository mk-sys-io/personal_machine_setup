"""Namespace exec ergonomics — the only place the shim pattern code lives.

Owns everything between the data-driven allowlist and the user's PATH: reading
grants, resolving binary names deterministically, rendering thin shims, and
deploying shims to /usr/local/bin. Thin shims are non-privileged ergonomics —
the allowlist is the gate, and `_validate_command` in namespace.py enforces it.

Gomplate-templated: the constants are quoted so the raw source stays valid
Python (parity with the netmgr module pattern).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .guards import require_unrestricted

# Gomplate-templated constants
NETNS_EXEC_ALLOWLIST = "{{ .Env.ARK_DATA_PATH }}/netns-exec-allowlist.txt"
MODE_FILE = "{{ .Env.ARK_DATA_PATH }}/mode"
USERNAME = "{{ .Env.USERNAME }}"

SHIM_DIR = "/usr/local/bin"
INET_NAME = "inet"
INET_SHIM = '#!/usr/bin/env bash\nif [ "$1" = "--ark-logs" ]; then\n    echo ~/.local/logs/netmgr.log\n    exit 0\nfi\nexec /usr/local/bin/netmgr namespace run "$@"\n'
THIN_SHIM_SHEBANG = "#!/usr/bin/env bash"
THIN_SHIM_MARKER = "# netmgr thin shim"
THIN_SHIM_MARKER_LEGACY = 'exec inet "'
CANONICAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class WrapperError(Exception):
    """Raised when shim generation fails."""


@dataclass(frozen=True)
class Grant:
    """A single allowlist entry.

    `path` is the raw token as stored; `realpath` is it resolved;
    `missing` is True when the resolved binary no longer exists on disk.
    """

    path: str
    realpath: str
    missing: bool


# ── Grant reading ─────────────────────────────────────────────────────────────

def read_grants() -> list[Grant]:
    """Parse the deployed allowlist (`path` lines) into Grant objects."""
    try:
        text = Path(NETNS_EXEC_ALLOWLIST).read_text()
    except OSError:
        return []
    grants: list[Grant] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path = line.split()[0]
        realpath = os.path.realpath(path)
        grants.append(
            Grant(
                path=path,
                realpath=realpath,
                missing=not os.path.exists(realpath),
            )
        )
    return grants


# ── Binary resolution ─────────────────────────────────────────────────────────

def resolve_binary(name_or_path: str) -> str:
    """Resolve a binary NAME or full path to its realpath.

    NAME is resolved via `shutil.which` against a fixed canonical PATH — the
    first match is the binary that actually runs. Full paths are validated and
    realpath'd. Rejects unresolvable or non-executable results.
    """
    if "/" in name_or_path:
        real = os.path.realpath(name_or_path)
    else:
        found = shutil.which(name_or_path, path=CANONICAL_PATH)
        if not found:
            raise WrapperError(
                f"binary not found on canonical PATH: {name_or_path}\n"
                f"  PATH: {CANONICAL_PATH}"
            )
        real = os.path.realpath(found)
    if not os.path.isfile(real):
        raise WrapperError(f"not a file: {real}")
    if not os.access(real, os.X_OK):
        raise WrapperError(f"not executable: {real}")
    return real


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_thin(realpath: str) -> str:
    """Thin shim body — route through inet, fall back to the raw binary only
    when inet could not launch at all.

    Supports --ark-logs to show the netmgr log file path (user-writable at
    ~/.local/logs/netmgr.log).

    inet exits 127/126 (bash command-not-found / not-executable) exactly when
    it FAILED to launch the command; a successfully launched app's exit code
    passes through untouched (exec semantics in run_cmd). The fallback is
    mode-gated and fail-closed: it only fires when /opt/ark/mode is readable
    and not `locked`, and it always prints a notice so a namespace bypass is
    never silent. Uses the absolute /usr/local/bin/inet path so the shim is
    independent of the caller's PATH (scripts, non-bashrc shells, cron).
    """
    name = os.path.basename(realpath)
    return (
        "#!/usr/bin/env bash\n"
        "# netmgr thin shim\n"
        'if [ "$1" = "--ark-logs" ]; then\n'
        '    echo ~/.local/logs/netmgr.log\n'
        "    exit 0\n"
        "fi\n"
        f'_mode="$(sed -n 1p {MODE_FILE} 2>/dev/null)"\n'
        f'/usr/local/bin/inet "{realpath}" "$@"\n'
        "_status=$?\n"
        'if [ -n "$_mode" ] && [ "$_mode" != "locked" ] && '
        '[[ "$_status" -eq 127 || "$_status" -eq 126 ]]; then\n'
        f'    echo "{name}: inet launch failed — running directly" >&2\n'
        f'    exec "{realpath}" "$@"\n'
        "fi\n"
        'exit "$_status"\n'
    )


# ── Deploy ────────────────────────────────────────────────────────────────────

def deploy() -> None:
    """Rebuild thin shims + inet, drop stale shims.

    Gated on unrestricted mode (blocked in focused/locked) and root-only:
    writes into /usr/local/bin. Writes nothing privileged — add/remove
    remain gated in namespace.py.
    """
    require_unrestricted()
    if os.geteuid() != 0:
        raise WrapperError("deploy requires root")
    grants = read_grants()
    current: set[str] = set()
    for g in grants:
        name = os.path.basename(g.realpath)
        current.add(name)
        _write_shim(name, render_thin(g.realpath))
    _write_shim(INET_NAME, INET_SHIM)
    _remove_stale_shims(current)


def _write_shim(name: str, body: str) -> None:
    path = Path(SHIM_DIR) / name
    path.write_text(body)
    os.chmod(path, 0o755)
    os.chown(path, 0, 0)


def _remove_stale_shims(current: set[str]) -> None:
    """Remove generated shims no longer backed by a thin grant."""
    shim_dir = Path(SHIM_DIR)
    for entry in shim_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.name == INET_NAME or entry.name in current:
            continue
        if _is_our_thin_shim(entry):
            entry.unlink()


def _is_our_thin_shim(path: Path) -> bool:
    """True when the file's head matches our generated thin-shim shape.

    Matches both the current marker (line 2 = `# netmgr thin shim`) and the
    legacy `exec inet "` shape so shims generated before the fallback change
    are still swept on the first post-change deploy.
    """
    try:
        with path.open() as f:
            first = f.readline().rstrip("\n")
            second = f.readline().rstrip("\n")
    except (OSError, UnicodeDecodeError):
        return False
    return first == THIN_SHIM_SHEBANG and (
        second.startswith(THIN_SHIM_MARKER)
        or second.startswith(THIN_SHIM_MARKER_LEGACY)
    )
