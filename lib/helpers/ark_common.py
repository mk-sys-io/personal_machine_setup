#!/usr/bin/env python3
"""Shared ark plumbing for the deploy/policy split.

Provides: SUDOERS_FILES manifest, BROWSER_SOURCES table, BLOCKLIST_FILES
+ modes, lazy ARK_* env merge (env_paths), full render environment
(render_env), privileged file deploy
(deploy_file), sudo'd existence test (priv_exists), silent sudo runner
(run_priv), print log facades, visudo discovery (find_visudo), and
structured visudo error attribution (parse_visudo_error, VisudoError).

Consumed by lib/60-ark-deploy.py (offline file+state deploy) and
lib/61-ark-policy.py (rendered policy pipeline + live actuation).
Ports the sudo-prefix pass, priv_exists, and visudo-PATH fixes from
plans/archive/60-ark.sh — the .sh is frozen and never fixed.

Stdlib only. Import-safe: importing never reads config, runs sudo,
prints, or exits.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_NAME = "config.txt"

# ARK_* keys env_paths() returns. ARK_REPO_ETC_PATH is intentionally
# excluded — deploy-time pointer, not needed by the module callers.
ARK_KEYS: tuple[str, ...] = (
    "ARK_DATA_PATH",
    "ARK_BIN_PATH",
    "ARK_LIB_PATH",
    "ARK_RENDER_PATHS",
)

# Sudoers file set — explicit list, no globs (repo convention: no glob
# expansion on system paths). Must match etc/ark/sudoers.d/ sources.
# Mirrors plans/archive/60-ark.sh SUDOERS_FILES.
SUDOERS_FILES: tuple[str, ...] = (
    "00-base",
    "10-diagnostics",
    "20-system",
    "30-network",
    "40-netmgr",
    "50-ark",
    "60-misc",
)

# Browser policy sources staged to $ARK_DATA_PATH. One row per browser:
# (repo-relative source, staged name, mode). Mirrors the plans/archive/60-ark.sh
# deploy_browser_policies table.
BROWSER_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("dotfiles/browsers/brave/policy.json.template", "brave-policy.json.template", "640"),
    ("dotfiles/browsers/chrome/policy.json.template", "chrome-policy.json.template", "640"),
)

# Blocklist files deployed repo -> $ARK_DATA_PATH/domains/focused.
# Mirrors the plans/archive/60-ark.sh deploy_blocklist loop (custom 640, rest 644).
BLOCKLIST_FILES: tuple[str, ...] = (
    "sources.json",
    "blocklist-custom.txt",
    "blocklist-exceptions.txt",
    "blocklist-exclude.txt",
)
BLOCKLIST_MODES: dict[str, str] = {"blocklist-custom.txt": "640"}
BLOCKLIST_DEFAULT_MODE = "644"


def blocklist_mode(name: str) -> str:
    """Deploy mode for a blocklist file (custom 640, default 644)."""
    return BLOCKLIST_MODES.get(name, BLOCKLIST_DEFAULT_MODE)


# ── log facades (texts mirror the bash log_* calls; shape copied from
# lib/20-packages.py — info to stdout, errors to stderr) ─────────────────


def log(msg: object) -> None:
    print(msg)


def log_step(msg: object) -> None:
    print(f">>> {msg}")


def log_ok(msg: object) -> None:
    print(f"  OK    {msg}")


def log_warn(msg: object) -> None:
    print(f"  WARN  {msg}")


def log_error(msg: object) -> None:
    print(f"  ERROR {msg}", file=sys.stderr)


# ── privileged runs ────────────────────────────────────────────────────


def run_priv(*args: str, timeout: int | None = None) -> tuple[int, str]:
    """Silent captured sudo run; never raises, never prompts.

    sudo -n fails fast when no timestamp is cached (install.py Stage 0
    runs sudo -v first) instead of hanging on a password prompt.
    Missing binary -> (127, ...). Mirrors the 20-packages run_quiet
    shape: a nonzero rc is data for the caller, not an exception.

    timeout=None (default) waits forever — correct for quick file ops.
    The timeout= backstop is a last resort: subprocess.run's expiry path
    SIGKILLs the DIRECT child (sudo), which cannot forward the signal —
    a privileged grandchild (netmgr) would be orphaned. Long-running
    privileged commands must instead wrap GNU timeout INSIDE the sudo
    call (`timeout 600 netmgr ...`) so root-side timeout TERM/KILLs the
    real process and exits 124; pass timeout=610 here purely as a
    backstop for a hung timeout(1). 2026-10-21: an unbounded
    `netmgr sources download` held 61-ark-policy hostage 12+ minutes.
    """
    try:
        proc = subprocess.run(
            ["sudo", "-n", *args], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + (exc.stderr or b"")
        return 124, f"timed out after {timeout}s: {' '.join(args)}\n{out.decode(errors='replace')}"
    except OSError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def priv_exists(*args: str) -> bool:
    """sudo'd existence test for root-only paths (sudo test).

    A bare os.path.exists as user lies under 750/440 live state; this
    returns the kernel truth instead. Condition-only: never raises
    under normal use (sudo failure -> False). Mirrors plans/archive/60-ark.sh
    priv_exists.
    """
    rc, _ = run_priv("test", *args)
    return rc == 0


def _norm_mode(mode: str) -> str:
    """Compare modes without leading-zero noise (stat %a emits none)."""
    return mode.lstrip("0") or "0"


def _priv_stat(dst: str) -> tuple[str, str]:
    """(mode, owner) of dst via sudo stat; raises RuntimeError on failure."""
    rc, out = run_priv("stat", "-c", "%a %U:%G", dst)
    if rc != 0:
        raise RuntimeError(f"ark_common: cannot stat {dst}: {out.strip()}")
    parts = out.split()
    if len(parts) != 2:
        raise RuntimeError(f"ark_common: unexpected stat output for {dst}: {out.strip()!r}")
    return parts[0], parts[1]


def deploy_file(src: str | Path, dst: str | Path, mode: str = "644", owner: str = "root:root") -> bool:
    """Privileged deploy with idempotency signal; mirrors plans/archive/60-ark.sh deploy_file.

    sudo cmp -s guard: identical bytes skip the copy but still enforce
    mode/owner (bash parity). Returns True when bytes were copied OR
    mode/owner were repaired, False when already correct. Any
    privileged step failing raises RuntimeError (fail-closed; False
    never means failure).
    """
    src_s, dst_s = str(src), str(dst)
    rc, _ = run_priv("cmp", "-s", src_s, dst_s)
    if rc != 0:
        for cmd in (("cp", src_s, dst_s), ("chmod", mode, dst_s), ("chown", owner, dst_s)):
            crc, out = run_priv(*cmd)
            if crc != 0:
                raise RuntimeError(f"ark_common: sudo {' '.join(cmd)} failed (rc={crc}): {out.strip()}")
        return True
    cur_mode, cur_owner = _priv_stat(dst_s)
    for cmd in (("chmod", mode, dst_s), ("chown", owner, dst_s)):
        crc, out = run_priv(*cmd)
        if crc != 0:
            raise RuntimeError(f"ark_common: sudo {' '.join(cmd)} failed (rc={crc}): {out.strip()}")
    return (_norm_mode(cur_mode), cur_owner) != (_norm_mode(mode), owner)


# ── env ────────────────────────────────────────────────────────────────


def _load_config_values() -> dict[str, str]:
    """Parse REPO_ROOT/config.txt (same rules as install.load_env).

    Split on first '=', strip one matched quote pair, skip blanks /
    '#'-comments / lines without '='. Empty value raises naming the key
    (D14 required-rule); missing file raises FileNotFoundError. Kept
    local (not imported) to avoid an install.py import cycle.
    """
    cfg = REPO_ROOT / CONFIG_NAME
    if not cfg.is_file():
        raise FileNotFoundError(f"{cfg} not found")
    values: dict[str, str] = {}
    for lineno, raw_line in enumerate(cfg.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        val = raw.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not key:
            continue
        if val == "":
            raise ValueError(f"{cfg.name}: {key} is empty (line {lineno})")
        values[key] = val
    return values


def env_paths() -> dict[str, str]:
    """Lazy ARK_* path merge: config.txt primed, real environment wins.

    Fill-unset-only (mirrors install.child_env): a key present in
    os.environ keeps its value. Read lazily on each call — never at
    import. A required key missing from both raises ValueError naming
    the key. ARK_RENDER_PATHS is returned raw (space-separated, as in
    config.txt); callers split it.
    """
    cfg = _load_config_values()
    paths: dict[str, str] = {}
    for key in ARK_KEYS:
        if key in os.environ:
            paths[key] = os.environ[key]
        elif key in cfg:
            paths[key] = cfg[key]
        else:
            raise ValueError(f"{CONFIG_NAME}: {key} is required (unset and not in environment)")
    return paths


def render_env() -> dict[str, str]:
    """Full environment for gomplate renders, including derived vars.

    sudo env_reset scrubs config.txt vars before privileged children
    run, and gomplate --missing-key error fails on any absent key — so
    render callers pass this dict explicitly via `sudo env K=V ...`
    (argv-form, no shell). Starts from os.environ (install.py
    child_env: config.txt primed, real env wins), then injects the
    runtime-derived values common.sh used to export for the bash path
    (common.sh:88-95 — USER_UID via id -u, TLE_FALLBACK_PATH default
    $HOME/go/bin/tle), which no Python module sources. Real-env-wins
    precedence holds for both derived keys. Must be computed in the
    unprivileged caller process — under sudo os.getuid() is 0, not the
    user UID the skuid templates need. Read lazily on each call, like
    env_paths().
    """
    env = dict(os.environ)
    env.setdefault("USER_UID", str(os.getuid()))
    env.setdefault(
        "TLE_FALLBACK_PATH", os.path.join(os.path.expanduser("~"), "go", "bin", "tle")
    )
    return env


# ── visudo ─────────────────────────────────────────────────────────────


def find_visudo() -> str | None:
    """Locate visudo without trusting user PATH.

    shutil.which first, then the /usr/sbin/visudo fallback (Debian
    keeps sbin off user PATH, so a bare lookup misses it — the archived
    plans/archive/60-ark.sh:212 bug). sudo itself is stage-0 guaranteed
    (tools/bootstrap.sh); this only heals the PATH gap. None when
    absent — the caller (61-ark-policy deploy_sudoers) fails loud
    naming the path.
    """
    found = shutil.which("visudo")
    if found:
        return found
    fallback = "/usr/sbin/visudo"
    if os.access(fallback, os.X_OK):
        return fallback
    return None


@dataclass
class VisudoError:
    """Attributed visudo failure: owning source, combined line, context."""

    owner: str | None
    line: int | None
    context: list[str]
    raw: str


_VISUDO_LINE_RE = re.compile(r":(\d+):\d+:|\bline (\d+)")


def parse_visudo_error(output: str, combined_text: str) -> VisudoError:
    """Attribute a visudo -c failure to its source file (plans/archive/60-ark.sh:217-227).

    Accepts both report shapes (path:LINE:COL: / syntax error near
    line N). owner resolves via the nearest '# --- name ---' marker at
    or above the error line; context is that line ±2, verbatim. No
    line info -> (None, None, [], raw); the caller logs raw.
    """
    match = _VISUDO_LINE_RE.search(output)
    if match is None:
        return VisudoError(owner=None, line=None, context=[], raw=output)
    err_line = int(match.group(1) if match.group(1) is not None else match.group(2))
    owner: str | None = None
    lines = combined_text.splitlines()
    for lineno, text in enumerate(lines, 1):
        if lineno > err_line:
            break
        if text.startswith("# --- ") and text.endswith(" ---"):
            owner = text[len("# --- ") : -len(" ---")]
    start = max(err_line - 2, 1)
    end = err_line + 2
    context = [text for lineno, text in enumerate(lines, 1) if start <= lineno <= end]
    return VisudoError(owner=owner, line=err_line, context=context, raw=output)
