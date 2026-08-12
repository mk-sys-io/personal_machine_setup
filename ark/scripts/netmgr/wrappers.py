"""Namespace exec ergonomics — the only place the shim/alias pattern code lives.

Owns everything between the data-driven allowlist and the user's PATH: reading
grants, resolving binary names deterministically, rendering thin shims and
generated bash aliases, syncing the alias block into a bashrc, and deploying
shims to /usr/local/bin. Thin shims are non-privileged ergonomics — the
allowlist is the gate, and `_validate_command` in namespace.py enforces it.

Gomplate-templated: the constants are quoted so the raw source stays valid
Python (parity with the netmgr module pattern).
"""
from __future__ import annotations

import os
import pwd
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

# Gomplate-templated constants
NETNS_EXEC_ALLOWLIST = "{{ .Env.ARK_DATA_PATH }}/netns-exec-allowlist.txt"
USERNAME = "{{ .Env.USERNAME }}"

SHIM_DIR = "/usr/local/bin"
INET_NAME = "inet"
INET_SHIM = '#!/usr/bin/env bash\nexec /usr/local/bin/netmgr namespace run "$@"\n'
THIN_SHIM_SHEBANG = "#!/usr/bin/env bash"
THIN_SHIM_MARKER = 'exec inet "'
CANONICAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

ALIAS_BLOCK_BEGIN = "# >>> netmgr aliases (generated) <<<"
ALIAS_BLOCK_END = "# <<<"


class WrapperError(Exception):
    """Raised when shim/alias generation fails."""


@dataclass(frozen=True)
class Grant:
    """A single allowlist entry.

    `path` is the raw first token as stored; `realpath` is it resolved;
    `arg` is the optional single first-arg restriction (None = thin grant);
    `missing` is True when the resolved binary no longer exists on disk.
    """

    path: str
    realpath: str
    arg: str | None
    missing: bool


# ── Grant reading ─────────────────────────────────────────────────────────────

def read_grants() -> list[Grant]:
    """Parse the deployed allowlist (`path [arg]` lines) into Grant objects."""
    try:
        text = Path(NETNS_EXEC_ALLOWLIST).read_text()
    except OSError:
        return []
    grants: list[Grant] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        path = parts[0]
        arg = parts[1] if len(parts) > 1 else None
        realpath = os.path.realpath(path)
        grants.append(
            Grant(
                path=path,
                realpath=realpath,
                arg=arg,
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
    """Thin shim body — exec the binary inside the namespace via inet."""
    return f'#!/usr/bin/env bash\nexec inet "{realpath}" "$@"\n'


def render_aliases(grants: list[Grant]) -> str:
    """Generated alias block — one `alias <basename>-<arg>` per dispatch grant.

    Bash aliases only rewrite the first word, so a plain `podman pull` can
    never be intercepted — the generated `podman-pull` alias is the explicit,
    sanctioned way to reach the dispatch grant.
    """
    lines = [ALIAS_BLOCK_BEGIN]
    for g in grants:
        if g.arg is None:
            continue
        alias = f"{os.path.basename(g.realpath)}-{g.arg}"
        lines.append(f"alias {alias}='inet {g.realpath} {g.arg}'")
    lines.append(ALIAS_BLOCK_END)
    return "\n".join(lines)


# ── bashrc alias-block sync ───────────────────────────────────────────────────

def write_bashrc(path: str | Path) -> None:
    """Rewrite the marked alias block in a bashrc file.

    Replaces the existing marked block (idempotent, no duplicates) or inserts
    one after the last `alias` line when absent. Everything else is preserved.
    """
    p = Path(path)
    block = render_aliases(read_grants())
    if not p.exists():
        p.write_text(block + "\n")
        return
    content = p.read_text()
    if ALIAS_BLOCK_BEGIN in content:
        pattern = re.compile(
            rf"^{re.escape(ALIAS_BLOCK_BEGIN)}.*?^{re.escape(ALIAS_BLOCK_END)}\n*",
            re.MULTILINE | re.DOTALL,
        )
        new_content = pattern.sub(block + "\n", content)
    else:
        new_content = _insert_after_alias_section(content, block)
    p.write_text(new_content)


def _insert_after_alias_section(content: str, block: str) -> str:
    lines = content.splitlines(keepends=True)
    insert_at: int | None = None
    for i, line in enumerate(lines):
        if line.strip().startswith("alias "):
            insert_at = i
    block_lines = [line + "\n" for line in block.splitlines()]
    if insert_at is None:
        return "".join(block_lines) + "\n" + content
    return "".join(
        lines[: insert_at + 1] + block_lines + lines[insert_at + 1 :]
    )


# ── Deploy ────────────────────────────────────────────────────────────────────

def deploy(bashrc_path: str | None = None) -> None:
    """Rebuild thin shims + inet, drop stale shims, optionally sync a bashrc.

    Ungated by mode (runs in focused so `ark lock` can regenerate shims) but
    root-only: writes into /usr/local/bin and can chown a bashrc back to
    USERNAME. Writes nothing privileged — add/remove remain gated in
    namespace.py.
    """
    if os.geteuid() != 0:
        raise WrapperError("deploy requires root")
    grants = read_grants()
    current: set[str] = set()
    for g in grants:
        if g.arg is not None:
            continue
        name = os.path.basename(g.realpath)
        current.add(name)
        _write_shim(name, render_thin(g.realpath))
    _write_shim(INET_NAME, INET_SHIM)
    _remove_stale_shims(current)
    if bashrc_path:
        write_bashrc(bashrc_path)
        pw = pwd.getpwnam(USERNAME)
        os.chown(bashrc_path, pw.pw_uid, pw.pw_gid)


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
    """True when the file's head matches our generated thin-shim shape."""
    try:
        with path.open() as f:
            first = f.readline().rstrip("\n")
            second = f.readline().rstrip("\n")
    except OSError:
        return False
    return first == THIN_SHIM_SHEBANG and second.startswith(THIN_SHIM_MARKER)
