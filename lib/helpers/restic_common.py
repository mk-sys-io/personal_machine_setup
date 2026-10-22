#!/usr/bin/env python3
"""Shared restic plumbing for Phase 40 (40-D).

Used by lib/40-restore.py (standalone post-install.py restore) and
lib/40-backup.py (standalone human backup CLI). Neither is trunk-wired —
isolation per docs/adr/012-restore-standalone-post-install.md.

Provides: repo env (RESTIC_REPOSITORY/HOST from config.txt; B2 AWS_* from
explicit params or ambient env), the /dev/tty secret-prompt chain (restic
password + B2 credentials — Bitwarden is the authoritative source, the
prompt is the runtime read), excludes stat, preflight 10/11/12
classification, and retry config for B2.

Secret chain (both CLIs, symmetric): explicit flag/env first, else a
/dev/tty prompt. The prompt clears IEXTEN around getpass (VLNEXT freeze)
and scrubs bracketed-paste markers; empty input warns of the impact and
offers one retype, declining proceeds empty and loudly warned.

Preflight codes (40-backup.md Standalone restore section):
    10  repo missing (list hosts, no auto-init)
    11  repo locked (--retry-lock observed; never automatic unlock)
    12  wrong password
    0   repo reachable

Exit codes (when run standalone for drills):
    0  pass
    1  failure
    2  usage error

Stdlib only. Import-safe: importing never runs restic, prompts, prints,
or exits.
"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
import tempfile
import termios
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HELPERS_DIR = REPO_ROOT / "lib" / "helpers"
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


def excludes_args() -> list[str]:
    """--exclude-file flag for file-data commands (backup only)."""
    return ["--exclude-file", str(excludes_path())]


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


def _warn(msg: str) -> None:
    print(f"restic-common: WARN {msg}", file=sys.stderr)


def _scrub_secret(raw: str) -> str:
    """Strip bracketed-paste markers and stray control chars from a tty secret.

    Terminals that honour bracketed paste wrap the payload in ESC[200~ /
    ESC[201~; those bytes would otherwise land inside the secret. With
    IEXTEN cleared, Ctrl+V arrives as a bare 0x16 instead of arming
    VLNEXT, so the scrub drops it too.
    """
    cleaned = raw.replace("\x1b[200~", "").replace("\x1b[201~", "")
    return "".join(c for c in cleaned if c >= " " and c != "\x7f")


def prompt_tty(prog: str, label: str) -> str | None:
    """Prompt on /dev/tty with `label`. Returns "" for empty input, None
    only when there is no usable terminal.

    Two terminal-level defects made the prompt look dead or corrupt
    passwords, both fixed here:
      1. VLNEXT freeze — getpass clears ECHO but leaves IEXTEN on, so a
         Ctrl+V (0x16, the kitty/Zed habit-paste when Ctrl+V is unbound)
         arms quote-next and the next Enter becomes a literal newline:
         the prompt never returns. IEXTEN is cleared around the getpass
         call so Ctrl+V degrades to a stray byte the scrub drops.
      2. Bracketed paste — ESC[200~ / ESC[201~ markers arrived inside
         the returned secret; _scrub_secret removes them.

    getpass opens its own O_NOCTTY handle for the termios echo-off, so
    the dup is safe to skip. os.open gives an unbuffered fd;
    os.fdopen in "w" with buffering=1 is line-buffered for the prompt
    write only. After entry, a `accepted (N chars)` line confirms the
    read landed (echo is off by design, so typing is invisible until
    then). KeyboardInterrupt propagates — ISIG is never touched.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError as exc:
        print(f"{prog}: no usable terminal for the password prompt: {exc}", file=sys.stderr)
        return None
    saved: list | None = None
    try:
        saved = termios.tcgetattr(fd)
        cleared = saved[:]
        cleared[3] &= ~termios.IEXTEN
        termios.tcsetattr(fd, termios.TCSANOW, cleared)
    except termios.error:
        saved = None  # not a termios-capable fd; getpass still works
    secret: str | None = None
    try:
        tty = os.fdopen(fd, "w", buffering=1)
    except OSError as exc:
        # Distinct from "no terminal": the tty opened but the prompt
        # machinery broke. Never silently degrade to None here.
        if saved is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, saved)
            except (termios.error, OSError):
                pass
        print(f"{prog}: password prompt failed on an open /dev/tty: {exc}", file=sys.stderr)
        return None
    try:
        base = label.rstrip().rstrip(":").rstrip()
        tty.write(f"{base} (input hidden by design — security):\n")
        tty.write("  (Ctrl+Shift+V or middle-click pastes) → ")
        tty.flush()
        shown = _scrub_secret(getpass.getpass("", stream=tty)).strip()
        tty.write(f"accepted ({len(shown)} chars)\n")
        tty.flush()
        secret = shown
    except OSError as exc:
        print(f"{prog}: password prompt failed on an open /dev/tty: {exc}", file=sys.stderr)
        secret = None
    finally:
        if saved is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, saved)
            except (termios.error, OSError):
                pass
        try:
            tty.close()
        except OSError:
            pass
    return secret


def _ask_yn(prog: str, question: str) -> bool:
    """Ask `question` on /dev/tty with echo on. Enter / anything but
    n|no answers True. No usable tty answers False (fail-open to the
    caller's proceed-empty path — never block headless forever)."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return False
    try:
        writer = os.fdopen(os.dup(fd), "w", buffering=1, encoding="utf-8")
    except OSError as exc:
        os.close(fd)
        print(f"{prog}: yes/no prompt failed on an open /dev/tty: {exc}", file=sys.stderr)
        return False
    try:
        writer.write(question)
        writer.flush()
        buf = b""
        while b"\n" not in buf and b"\r" not in buf:
            chunk = os.read(fd, 1)
            if not chunk:
                break
            buf += chunk
        answer = buf.decode("utf-8", errors="replace").strip().lower()
    except OSError as exc:
        print(f"{prog}: yes/no prompt failed on an open /dev/tty: {exc}", file=sys.stderr)
        return False
    finally:
        try:
            writer.close()  # closes the dup only
        except OSError:
            pass
        os.close(fd)
    return answer not in ("n", "no")


def _empty_secret_flow(prog: str, label: str, name: str, impact: str) -> str:
    """First entry came back empty: warn with `impact`, offer one retype,
    return the final value (possibly ""). Never raises — the caller
    proceeds with whatever is returned and owns any further warning."""
    _warn(f"empty {name} — {impact}")
    if not _ask_yn(prog, f"{prog}: retype? [Y/n]: "):
        _warn(f"continuing with an empty {name} — operator declined the retype")
        return ""
    again = prompt_tty(prog, label) or ""
    if not again:
        _warn(f"continuing with an empty {name} — re-entry was also empty")
    return again


def resolve_password(
    password_file: str | None,
    password_command: str | None,
    prog: str,
    prompt_label: str,
) -> tuple[str | None, str | None, Path | None]:
    """Return (password_file, password_command, temp_to_cleanup).

    Chain: explicit password_file -> explicit password_command -> TTY
    prompt. Empty TTY input warns of the impact and offers one retype;
    declining (or a second empty) proceeds with an empty passphrase,
    loudly warned. Headless with no file/command fails naming the flag.

    The prompted secret is written to a 0600 temp file and passed as
    password_file (restic's --password-file path) so it never lands in
    argv or the process env of the restic invocation. Caller owns the
    returned temp path (unlink in a finally).
    """
    if password_file:
        p = Path(password_file).expanduser()
        if not p.is_file():
            raise ValueError(f"--restore-password-file not found: {p}")
        return str(p), None, None
    if password_command:
        return None, password_command, None
    secret = prompt_tty(prog, prompt_label)
    if secret is None:
        raise ValueError(
            "no terminal for interactive prompt — re-run with --restore-password-file "
            f"or --password-command (or set RESTIC_PASSWORD_COMMAND); prog={prog}"
        )
    if not secret:
        secret = _empty_secret_flow(
            prog,
            prompt_label,
            "restic repo password",
            "an empty passphrase means anyone with repo access can restore this backup",
        )
    fd, tmp = tempfile.mkstemp(prefix=".restic-pw.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(secret)
    finally:
        del secret
    return tmp, None, Path(tmp)


def resolve_b2_credentials(prog: str) -> tuple[str, str]:
    """(key_id, secret_access_key) for the S3 backend.

    Chain: ambient AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY if both are
    set -> prompt each missing value on /dev/tty, mirroring
    resolve_password's shape. Both values live in Bitwarden (backup.md
    custody table); neither is a filename or a flag, so nothing new is
    added to the command line.

    Probed per field, so a half-set ambient pair prompts only for what is
    missing instead of re-asking for both. Never logged: the values reach
    the restic subprocess env and nowhere else.
    """
    key_id = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if key_id and secret:
        return key_id, secret
    if key_id or secret:
        _warn("ambient AWS_* pair incomplete — prompting for the missing B2 value")
    else:
        _warn("no ambient AWS_* — prompting for the Bitwarden-held B2 values")
    if not key_id:
        key_id = prompt_tty(prog, f"{prog}: B2 keyID (Bitwarden-held):") or ""
        if not key_id:
            key_id = _empty_secret_flow(
                prog,
                f"{prog}: B2 keyID (Bitwarden-held):",
                "B2 keyID",
                "b2 upload will fail until the B2 credentials are supplied (Bitwarden custody table)",
            )
    if not secret:
        secret = prompt_tty(prog, f"{prog}: B2 applicationKey (Bitwarden-held):") or ""
        if not secret:
            secret = _empty_secret_flow(
                prog,
                f"{prog}: B2 applicationKey (Bitwarden-held):",
                "B2 applicationKey",
                "b2 upload will fail until the B2 credentials are supplied (Bitwarden custody table)",
            )
    if not key_id or not secret:
        _warn(
            "proceeding with incomplete B2 credentials — b2 upload steps will fail "
            "until the Bitwarden-held values are supplied (Bitwarden custody table)"
        )
    return key_id, secret


def build_repo_env(
    password_command: str | None = None,
    password_file: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> RepoEnv:
    """Build the restic subprocess env. Secrets never touch disk or logs.

    B2 credentials come from the explicit params when both are given;
    otherwise any ambient AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (set by
    the caller after resolve_b2_credentials, or exported by the operator)
    pass through the environ copy untouched. Callers that own an
    interactive session prompt via resolve_b2_credentials and pass the
    values in; headless callers must export the ambient pair.

    Password: password_file (RESTIC_PASSWORD_FILE) wins; else
    password_command or the ambient RESTIC_PASSWORD_COMMAND env. With
    neither, restic falls back to its own prompt when attached to a
    terminal, or fails loud when not — never a silent default.
    """
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
    if access_key_id is not None and secret_access_key is not None:
        env["AWS_ACCESS_KEY_ID"] = access_key_id
        env["AWS_SECRET_ACCESS_KEY"] = secret_access_key
    if password_file:
        env["RESTIC_PASSWORD_FILE"] = password_file
        pw_cmd = ""
    else:
        pw_cmd = password_command or os.environ.get("RESTIC_PASSWORD_COMMAND") or ""
        if pw_cmd:
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
    12 = wrong password (caller re-prompts <=3 then fails naming the Bitwarden vault).
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
