#!/usr/bin/env python3
"""Shared restic plumbing for Phase 40 (40-D).

Used by lib/40-restore.py (standalone post-install.py restore) and
lib/40-backup.py (standalone human backup CLI). Neither is trunk-wired —
isolation per docs/adr/012-restore-standalone-post-install.md.

Provides: repo env (RESTIC_REPOSITORY/HOST from config.txt, AWS_* from
gopass services/b2 into the restic subprocess env only), default
password command (lib/helpers/gopass.sh services/restic key), excludes
stat, preflight 10/11/12 classification, and retry config for B2.

Preflight codes (40-backup.md Standalone restore section):
    10  repo missing (list hosts, no auto-init)
    11  repo locked (--retry-lock observed; never automatic unlock)
    12  wrong password
    0   repo reachable

Exit codes (when run standalone for drills):
    0  pass
    1  failure
    2  usage error

Stdlib only. Import-safe: importing never runs restic, reads gopass,
prints, or exits.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HELPERS_DIR = REPO_ROOT / "lib" / "helpers"
GOPASS_READER = HELPERS_DIR / "gopass.sh"
EXCLUDES_REL = Path("etc/restic/excludes.txt")
CONFIG_NAME = "config.txt"

RETRY_LOCK = "10m"
RETRY_MAX_ATTEMPTS = 5

# B2 Thursday window / EU-Central incident strings restic retries absorb.
# Kept as documentation for the human CLI; automated invocations rely on
# restic's own retry plus --retry-lock, never on string matching.
RETRY_HINTS = ("connection reset", "timeout", "temporary", "515", "503")


@dataclass
class RepoEnv:
    repository: str
    host: str
    env: dict[str, str]
    password_command: str


def excludes_path() -> Path:
    return REPO_ROOT / EXCLUDES_REL


def stat_excludes() -> Path:
    """Fail loud naming the exact path when the excludes file is missing."""
    p = excludes_path()
    try:
        st = p.stat()
    except OSError:
        print(f"restic-common: ERROR missing excludes file: {p}", file=sys.stderr)
        print(f"  Fix: re-run ./install.py phase 40-B (ships {EXCLUDES_REL})", file=sys.stderr)
        raise
    if st.st_size == 0:
        print(f"restic-common: ERROR excludes file is empty: {p}", file=sys.stderr)
        raise FileNotFoundError(str(p))
    return p


def load_config_values() -> dict[str, str]:
    """Read non-secret restic values from config.txt (no fallback)."""
    cfg = REPO_ROOT / CONFIG_NAME
    values: dict[str, str] = {}
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"restic-common: ERROR cannot read {cfg}: {exc}", file=sys.stderr)
        raise
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, raw = s.partition("=")
        key = key.strip()
        val = raw.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key in ("RESTIC_REPOSITORY", "RESTIC_HOST") and val:
            values[key] = val
    return values


def default_password_command() -> str:
    """Default --password-command value (gopass reader, never a file)."""
    return f"{GOPASS_READER} services/restic key"


def read_gopass_field(entry: str, field: str) -> str:
    """Read one gopass field via the canonical reader executable."""
    if not GOPASS_READER.is_file():
        raise FileNotFoundError(f"gopass reader missing: {GOPASS_READER}")
    try:
        r = subprocess.run(
            ["bash", str(GOPASS_READER), entry, field],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gopass {entry} {field} timed out (agent/pinentry stuck?) — check gpg-agent.conf") from exc
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        hint = tail[-1] if tail else f"gopass insert {entry} {field}"
        raise RuntimeError(f"gopass {entry} {field} unreadable: {hint}")
    value = (r.stdout or "").strip()
    if not value:
        raise RuntimeError(f"gopass entry '{entry}' field '{field}' is empty")
    return value


def build_repo_env(
    password_command: str | None = None,
    password_file: str | None = None,
) -> RepoEnv:
    """Build the restic subprocess env. Secrets never touch disk or logs."""
    cfg = load_config_values()
    repository = os.environ.get("RESTIC_REPOSITORY") or cfg.get("RESTIC_REPOSITORY", "")
    host = os.environ.get("RESTIC_HOST") or cfg.get("RESTIC_HOST", "")
    if not repository:
        raise RuntimeError("RESTIC_REPOSITORY unset (config.txt + env both empty)")
    if not host:
        raise RuntimeError("RESTIC_HOST unset (config.txt + env both empty)")
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = repository
    env["RESTIC_HOST"] = host
    # S3 creds live in gopass services/b2 -> subprocess env only.
    env["AWS_ACCESS_KEY_ID"] = read_gopass_field("services/b2", "keyID")
    env["AWS_SECRET_ACCESS_KEY"] = read_gopass_field("services/b2", "applicationKey")
    if password_file:
        env["RESTIC_PASSWORD_FILE"] = password_file
        pw_cmd = ""
    else:
        pw_cmd = password_command or os.environ.get("RESTIC_PASSWORD_COMMAND") or default_password_command()
        env["RESTIC_PASSWORD_COMMAND"] = pw_cmd
    # Never log secret values; keys only.
    return RepoEnv(repository=repository, host=host, env=env, password_command=pw_cmd)


def base_args(password_command: str | None = None, password_file: str | None = None) -> list[str]:
    """Base restic args: retry-lock on every automated invocation."""
    args: list[str] = [f"--retry-lock={RETRY_LOCK}"]
    if password_file:
        args += ["--password-file", password_file]
    elif password_command or os.environ.get("RESTIC_PASSWORD_COMMAND"):
        args += ["--password-command", password_command or os.environ["RESTIC_PASSWORD_COMMAND"]]
    return args


def restic_binary() -> str | None:
    return shutil.which("restic")


def classify_stderr(stderr: str) -> int:
    """Map restic stderr to preflight 10/11/12 (0 = none matched)."""
    low = (stderr or "").lower()
    if "wrong password" in low or "incorrect password" in low or "ciphertext verification failed" in low:
        return 12
    if "repository is already locked" in low or "failed to lock" in low or "locked by" in low:
        return 11
    if "no matching host" in low or "repository not found" in low or "no repository" in low:
        return 10
    if "wrong password" in low:
        return 12
    return 0


def preflight(repo_env: RepoEnv) -> tuple[int, str]:
    """Run `restic snapshots --latest 1`-class probe; return (code, detail).

    10 = missing (caller lists hosts, never auto-inits).
    11 = locked (caller documents manual `restic unlock` after confirming
         no live holder; B2 stale locks are restic's documented mode).
    12 = wrong password (caller re-prompts <=3 then paperkey hint).
    """
    exe = restic_binary()
    if exe is None:
        return 10, "restic binary missing (phase 40-A: packages/apt.txt)"
    cmd = ["restic", *base_args(), "snapshots", "--latest", "1", "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=repo_env.env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 11, f"probe failed (retryable): {exc}"
    if r.returncode == 0:
        return 0, "repo reachable"
    code = classify_stderr(r.stderr or "")
    if code == 10:
        return 10, "repo missing or no snapshots for host (no auto-init)"
    if code == 11:
        return 11, "repo locked (manual `restic unlock` after confirming no live holder)"
    if code == 12:
        return 12, "wrong password"
    # Unknown non-zero: surface stderr tail, treat as lock-adjacent retryable.
    tail = ((r.stderr or "").strip().splitlines() or ["unknown restic error"])[-1]
    return 11, f"probe rc={r.returncode}: {tail}"


def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print(f"usage: {Path(argv[0]).name} [--stat-excludes|--probe]", file=sys.stderr)
        return 0
    if len(argv) == 2 and argv[1] == "--stat-excludes":
        try:
            stat_excludes()
        except (OSError, FileNotFoundError):
            return 1
        print(f"restic-common: excludes OK: {excludes_path()}")
        return 0
    if len(argv) == 2 and argv[1] == "--probe":
        try:
            repo_env = build_repo_env()
        except (OSError, RuntimeError) as exc:
            print(f"restic-common: ERROR {exc}", file=sys.stderr)
            return 1
        code, detail = preflight(repo_env)
        print(f"restic-common: preflight={code} {detail}")
        return 0 if code == 0 else 1
    print(f"usage: {Path(argv[0]).name} [--stat-excludes|--probe]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
