#!/usr/bin/python3
"""Shared library for cask and uncask operations."""

from __future__ import annotations

import errno
import glob
import http.client
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from http.client import HTTPResponse
from typing import NoReturn, TypedDict, cast

import immutable_lib
import opslog
from netmgr.policies import BROWSERS

from cask.clipboard import clear_clipboard

# ── Strict env lookup ────────────────────────────────────────────────────────
# Fails immediately if config.env wasn't sourced. No silent misconfiguration.

MIKE: pwd.struct_passwd = pwd.getpwnam("{{ .Env.USERNAME }}")
MIKE_UID: int = MIKE.pw_uid
MIKE_GID: int = MIKE.pw_gid
HOME_DIR: str = MIKE.pw_dir
CASK_DIR: str = "{{ .Env.ARK_DATA_PATH }}/cask"  # casked files (root-owned)
CASK_WORK_DIR: str = os.path.join(HOME_DIR, ".local", "share", "cask")  # working dir (user-owned)
MODE_FILE: str = "{{ .Env.ARK_DATA_PATH }}/mode"

# ── Adapter PATH resolution ──────────────────────────────────────────────────
# Ensure etc/ark/adapters/ is in PATH so cask/uncask find adapters by name.

ARK_LIB: str = os.environ.get("ARK_LIB_PATH", "/usr/local/lib/ark")
os.environ["PATH"] = f"{ARK_LIB}:{os.environ['PATH']}"

# ruff: ignore[SIM905] -- rendered config value, not a literal; split() needed at runtime
SHELL_HISTORY_FILES: Sequence[str] = "{{ .Env.SHELL_HISTORY_FILES }}".split()

# ── Error handling + signal path ─────────────────────────────────────────────
# opslog owns the logger tag (set via opslog.configure()); _COMPONENT only feeds
# the SIGTERM error path, which must know which session label to close.

_signal_component: str = "cask"
_COMPONENT_LABELS: dict[str, str] = {
    "cask": "Cask",
    "mcask": "Mobile cask",
    "uncask": "Uncask",
}


def set_component(component: str) -> None:
    """Set the component used by the SIGTERM error path (after opslog.configure)."""
    global _signal_component
    _signal_component = component


class CaskError(Exception):
    pass


def ensure_cask_dirs() -> None:
    os.makedirs(CASK_DIR, exist_ok=True)
    os.chown(CASK_DIR, 0, 0)
    os.chmod(CASK_DIR, 0o750)
    os.makedirs(CASK_WORK_DIR, exist_ok=True)
    os.chown(CASK_WORK_DIR, MIKE_UID, MIKE_GID)


def emergency_exit(component: str) -> NoReturn:
    opslog.error("Aborting")
    opslog.end_session(component, "FAILED")
    label = _COMPONENT_LABELS.get(component, component.capitalize())
    print(f"\n[ERROR] {label} failed — see the log for details", file=sys.stderr)
    sys.exit(1)


def handle_signal(signum: int, _frame: object) -> None:
    opslog.error(f"Received signal {signum}, aborting")
    emergency_exit(_signal_component)


_ = signal.signal(signal.SIGTERM, handle_signal)


# ── Pre-flight gates ─────────────────────────────────────────────────────────


def gate_network() -> None:
    """Pre-flight network gates — delegates to netmgr.guards.check_prereqs().

    Single source of truth for the DNS/TCP/TLE checks. Translates
    NetworkError/PrereqError to CaskError so mcask/uncask callers are
    unchanged.
    """
    from netmgr import guards

    try:
        guards.check_prereqs()
    except (guards.NetworkError, guards.PrereqError) as e:
        raise CaskError(str(e)) from None


def gate_tle() -> str:
    candidates: list[str] = ["{{ .Env.TLE_PRIMARY_PATH }}", "{{ .Env.TLE_FALLBACK_PATH }}"]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise CaskError("tle not found at {{ .Env.TLE_PRIMARY_PATH }} or {{ .Env.TLE_FALLBACK_PATH }}")


def gate_cred_file(path: str, must_be_empty: bool = False, exists_msg: str | None = None) -> None:
    if not os.path.isfile(path):
        try:
            os.stat(path)
        except PermissionError:
            msg = f"{path} not readable (permission denied).\n"
        else:
            msg = f"{path} not found.\n"
        if exists_msg:
            msg += exists_msg
        raise CaskError(msg)
    size = os.path.getsize(path)
    if must_be_empty and size != 0:
        raise CaskError(
            f"{path} must be empty.\n       Clear it with:\n         : > {path}"
        )
    if not must_be_empty and size == 0:
        raise CaskError(f"{path} is empty")


# ── Display ──────────────────────────────────────────────────────────────────


def prompt_manual_copy(label: str = "password") -> None:
    print(f"Select and copy the {label} above manually")


# ── Browser data clear ──────────────────────────────────────────────────────

# Per-browser data files to remove from each profile live in the BROWSERS
# registry (netmgr.policies) under `cleanup_files` — the single source of
# truth. Chromium uses its own file names in a fixed "Default" profile;
# Firefox-family (LibreWolf) uses SQLite/JSON files in a "*.default*" dir.


def clear_browser_data() -> None:
    opslog.set_step("Clearing browser cache, cookies, history")

    for name, browser in BROWSERS.items():
        cache_base = os.path.join(HOME_DIR, str(browser["cache_dir"]))
        if os.path.isdir(cache_base):
            try:
                shutil.rmtree(cache_base, ignore_errors=True)
                opslog.ok(f"Removed {name} cache")
            except OSError as e:
                opslog.warn(f"Failed to remove {name} cache: {e}")

        cleanup = cast(list[str], browser["cleanup_files"])
        profile_base = os.path.join(HOME_DIR, str(browser["profile_dir"]))
        for profile in glob.glob(os.path.join(profile_base, str(browser["profile_glob"]))):
            if not os.path.isdir(profile):
                continue
            for fname in cleanup:
                fpath = os.path.join(profile, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                        opslog.ok(f"Deleted {name}/{os.path.basename(profile)}/{fname}")
                    except OSError as e:
                        opslog.warn(f"Failed to delete {name}/{fname}: {e}")


# ── History wipe ─────────────────────────────────────────────────────────────


def wipe_history() -> None:
    opslog.set_step("Wiping shell history")
    wiped = 0
    for name in SHELL_HISTORY_FILES:
        path = os.path.join(HOME_DIR, name)
        if os.path.isfile(path):
            try:
                _ = subprocess.run(
                    ["shred", "-u", path], capture_output=True, timeout=10, check=False
                )
                wiped += 1
            except (subprocess.TimeoutExpired, OSError) as e:
                opslog.warn(f"Failed to wipe {name}: {e}")
    opslog.ok(f"Shell history wiped ({wiped} files)")


# ── Decrypt time check ────────────────────────────────────────────────────────

DRAND_CACHE: dict[str, int] = {}


class CaskMetadata(TypedDict):
    cask_timestamp: int
    tle_duration: str


def _load_drand_cache() -> bool:
    if DRAND_CACHE:
        return True
    url = "https://{{ .Env.DRAND_HOST }}/{{ .Env.DRAND_CHAIN_HASH }}/info"
    try:
        with cast(HTTPResponse, urllib.request.urlopen(url, timeout=10)) as resp:
            info = cast(dict[str, object], json.loads(resp.read()))
        genesis = info.get("genesis_time")
        period = info.get("period")
        if not isinstance(genesis, int) or not isinstance(period, int):
            opslog.warn("Invalid drand chain info: expected integer genesis_time and period")
            return False
        DRAND_CACHE["genesis"] = genesis
        DRAND_CACHE["period"] = period
        return True
    except (OSError, ValueError, KeyError, http.client.HTTPException) as e:
        opslog.warn(f"Failed to fetch drand chain info: {e}")
        return False


def _load_cask_metadata() -> CaskMetadata | None:
    meta_path = os.path.join(CASK_DIR, "metadata.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path) as f:
            raw = cast(dict[str, object], json.load(f))
    except (OSError, ValueError) as e:
        opslog.warn(f"Failed to read {meta_path}: {e}")
        return None
    ts = raw.get("cask_timestamp")
    dur = raw.get("tle_duration")
    if not isinstance(ts, int) or not isinstance(dur, str):
        opslog.warn("Invalid metadata.json: expected integer cask_timestamp and string tle_duration")
        return None
    return {"cask_timestamp": ts, "tle_duration": dur}


def check_decrypt_time(tle_bin: str, cask_path: str) -> bool:
    """Return True if the timelock has expired (decryption is allowed).

    NOTE (fail-open): unlike ark's ``_tle_gate`` (fail-closed), the unknown
    states below — tle failed with no drand round, or drand unreachable —
    return True (treat as expired). This is a deliberate availability-over-
    strictness trade for the user-facing uncask/mcask tools: on uncertainty
    we let the user try and get a clear decrypt error, rather than a lockout.
    The clock comparison below is the same ``time.time()`` vs drand-derived
    ``unlock_ts`` shape as ``_tle_gate``'s fallback — not exploitable today
    (no sudo in focused mode; decryption stays beacon-bound), see ark.py.
    """
    if not os.path.isfile(cask_path):
        raise CaskError(f"Cask file not found: {cask_path}")

    r = subprocess.run(
        [tle_bin, "-d", "-o", "/dev/null", cask_path],
        capture_output=True,
        text=True,
        timeout=int("{{ .Env.TLE_TIMEOUT }}"),
        check=False,
    )

    if r.returncode == 0:
        return True

    match = re.search(r"round (\d+)", r.stderr)
    if not match:
        return True

    round_num = int(match.group(1))

    if not DRAND_CACHE and not _load_drand_cache():
        return True

    unlock_ts = DRAND_CACHE["genesis"] + (round_num - 1) * DRAND_CACHE["period"]
    unlock_dt = datetime.fromtimestamp(unlock_ts, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = unlock_dt - now

    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        remaining = f"{days} days, {hours} hours, {minutes} minutes"
    elif hours > 0:
        remaining = f"{hours} hours, {minutes} minutes"
    else:
        remaining = f"{minutes} minutes"

    print()
    print("============================================")
    print("  Timelock has NOT expired yet")
    print("============================================")
    print()
    print(f"  Will be available at: {unlock_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  ({remaining} from now)")
    print()
    print("  Wait for the timelock to expire, then run uncask again.")
    print()
    return False


def get_remaining_tle_time(tle_bin: str, cask_path: str) -> int:
    if not os.path.isfile(cask_path):
        raise CaskError(f"Cask file not found: {cask_path}")

    r = subprocess.run(
        [tle_bin, "-d", "-o", "/dev/null", cask_path],
        capture_output=True, text=True, timeout=int("{{ .Env.TLE_TIMEOUT }}"), check=False
    )

    if r.returncode == 0:
        raise CaskError("TLE has already expired")

    match = re.search(r"round (\d+)", r.stderr)
    if not match:
        raise CaskError("Could not determine TLE round from tle output")

    round_num = int(match.group(1))

    if not DRAND_CACHE and not _load_drand_cache():
        meta = _load_cask_metadata()
        if meta is None:
            raise CaskError("Cannot determine remaining TLE time (drand unreachable, no metadata)")
        cask_ts = meta["cask_timestamp"]
        dur_str = meta["tle_duration"]
        dur_secs = parse_duration(dur_str)
        elapsed = int(time.time()) - cask_ts
        remaining = dur_secs - elapsed
        return max(remaining, 0)

    unlock_ts = DRAND_CACHE["genesis"] + (round_num - 1) * DRAND_CACHE["period"]
    now = int(time.time())
    remaining = unlock_ts - now
    return max(remaining, 0)


def store_cask_metadata(duration: str) -> None:
    meta: CaskMetadata = {"cask_timestamp": int(time.time()), "tle_duration": duration}
    path = os.path.join(CASK_DIR, "metadata.json")
    try:
        immutable_lib.clear_immutable(path)
    except immutable_lib.ImmutableError as e:
        raise CaskError(f"cannot rewrite metadata.json: {e}") from e
    tmp = path + ".new"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, path)
    _ = os.chown(path, 0, 0)
    os.chmod(path, 0o644)


# ── Interactive prompts ──────────────────────────────────────────────────────


def compute_expiry(duration: str) -> str:
    human = (
        duration.replace("m", " minutes").replace("h", " hours").replace("d", " days")
    )
    try:
        r = subprocess.run(
            ["date", "-u", "-d", f"+{human}", "+%Y-%m-%d %H:%M:%S UTC"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
        opslog.warn(
            f"date command failed (exit {r.returncode}) for duration '{duration}'",
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        opslog.warn(f"date command raised: {e}")
    return "(unknown)"


def prompt_duration() -> str:
    print("Select timelock duration:")
    print("  1) 30 minutes")
    print("  2) 1 hour")
    print("  3) 3 hours")
    print("  4) 1 day")
    print("  5) 3 days")
    print("  6) 7 days")
    print("  7) Custom (e.g. 30m, 4h, 7d)")
    try:
        choice = input().strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        opslog.end_session(_signal_component, "CANCELLED")
        sys.exit(0)

    durations = {"1": "30m", "2": "1h", "3": "3h", "4": "1d", "5": "3d", "6": "7d"}
    if choice in durations:
        return durations[choice]

    if choice == "7":
        try:
            dur = input(
                "Enter duration (e.g. 30m, 4h, 7d — case sensitive, no capitals): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            opslog.end_session(_signal_component, "CANCELLED")
            sys.exit(0)
        if re.match(r"^\d+[mhd]$", dur):
            return dur
        print("Error: invalid format. Use e.g. 30m, 4h, 7d", file=sys.stderr)
        opslog.end_session(_signal_component, "FAILED")
        sys.exit(1)

    if not choice:
        print("Cancelled.")
        opslog.end_session(_signal_component, "CANCELLED")
        sys.exit(0)
    else:
        print("Invalid choice", file=sys.stderr)
        opslog.end_session(_signal_component, "FAILED")
        sys.exit(1)


# ── Duration helpers ─────────────────────────────────────────────────────────


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h" if m == 0 else f"{h}h{m}m"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d" if h == 0 else f"{d}d{h}h"


def parse_duration(s: str) -> int:
    s = s.strip().lower()
    match = re.match(r"^(\d+)([mhd])$", s)
    if not match:
        raise ValueError(f"Invalid duration: {s!r} (use e.g. 30m, 4h, 7d)")
    val, unit = int(match.group(1)), match.group(2)
    return val * {"m": 60, "h": 3600, "d": 86400}[unit]


def prompt_lock_duration(remaining: int) -> int:
    presets = [1800, 3600, 7200]
    valid = [s for s in presets if s <= remaining]
    options: dict[str, int] = {}
    idx = 1
    for s in valid:
        options[str(idx)] = s
        idx += 1
    options[str(idx)] = remaining
    remaining_label = format_duration(remaining)

    print("Select lock duration:")
    for k, s in options.items():
        if s == remaining:
            print(f"  {k}) Lock for remaining TLE time ({remaining_label})")
        else:
            print(f"  {k}) {format_duration(s)}")
    print(f"  {idx + 1}) Custom (e.g. 30m, 4h)")

    try:
        choice = input().strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)

    if choice in options:
        return options[choice]
    if choice == str(idx + 1):
        try:
            raw = input("Enter duration (e.g. 30m, 4h, 7d): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        secs = parse_duration(raw)
        if secs > remaining:
            sys.exit(f"Error: {format_duration(secs)} exceeds remaining " +
                     f"TLE time ({remaining_label})")
        return secs
    if not choice:
        print("Aborted.")
        sys.exit(0)
    sys.exit("Error: invalid choice")


def confirm(
    label: str, cred_path: str, duration: str, expiry: str, items: list[str]
) -> None:
    print()
    print("=============================================")
    print(f"  You are about to cask the {label}.")
    print("=============================================")
    print()
    print(f"  Timelock:     {duration}")
    print(f"  Expires:      {expiry}")
    print(f"  Credentials:  {cred_path}")
    print()
    print("  This will:")
    for item in items:
        print(f"    - {item}")
    print()
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        opslog.end_session(_signal_component, "CANCELLED")
        sys.exit(0)
    if answer not in ("y", "yes"):
        print("Cancelled.")
        opslog.end_session(_signal_component, "CANCELLED")
        sys.exit(0)


# ── Core recipe ──────────────────────────────────────────────────────────────


def cask_credentials_file(
    tle_bin: str,
    cred_path: str,
    cask_path: str,
    duration: str,
    *,
    after_encrypt: Callable[[], None] | None = None,
) -> None:
    """Cask credentials: atomic encrypt -> verify -> optional hook -> shred ->
    cleanup -> metadata. No gates or prompts inside — the caller owns the
    preconditions (verified once at the boundary)."""
    opslog.set_step("Cask credentials")
    encrypt(tle_bin, cred_path, cask_path, duration)
    if after_encrypt is not None:
        after_encrypt()
    shred_file(cred_path)
    clear_clipboard(purge=True)
    wipe_history()
    clear_browser_data()
    store_cask_metadata(duration)
    opslog.ok("Credentials casked")


# ── File sanitization ────────────────────────────────────────────────────────


def shred_file(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        size = os.path.getsize(path)
        with open(path, "wb") as f:
            _ = f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        opslog.warn(f"Failed to overwrite {path}: {e}")

    try:
        _ = subprocess.run(
            ["shred", "-u", path], capture_output=True, check=True, timeout=30
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        opslog.warn(f"shred failed: {e}, attempting rm -f")
        _ = subprocess.run(["rm", "-f", path], capture_output=True, check=False)

    if os.path.exists(path):
        raise CaskError(f"Failed to shred {path} — file still exists")


# ── Encryption ───────────────────────────────────────────────────────────────


def encrypt(tle_bin: str, cred_path: str, cask_path: str, duration: str) -> None:
    opslog.set_step("Preparing encryption")

    # Self-heal any crash-interrupted state, then clear the flag BEFORE any
    # mutation — a failed clear aborts with the old cask still intact/protected.
    immutable_lib.check_available()
    _ = immutable_lib.repair_immutable([cask_path])
    try:
        immutable_lib.clear_immutable(cask_path)
    except immutable_lib.ImmutableError as e:
        raise CaskError(f"cannot rewrite cask file: {e}") from e

    tmpdir = tempfile.mkdtemp(prefix="cask_", dir=CASK_WORK_DIR)
    try:
        tmp_cred = os.path.join(tmpdir, "credentials")
        _ = shutil.copy2(cred_path, tmp_cred)

        tmp_cask = os.path.join(tmpdir, "cask")
        opslog.set_step(f"Running tle -e -D {duration}")
        try:
            r = subprocess.run(
                [tle_bin, "-e", "-D", duration, "--armor", "-o", tmp_cask, tmp_cred],
                capture_output=True,
                text=True,
                timeout=int("{{ .Env.TLE_TIMEOUT }}"),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CaskError("tle encryption timed out after {{ .Env.TLE_TIMEOUT }} seconds") from None

        if r.returncode != 0:
            stderr_msg = r.stderr.strip() if r.stderr.strip() else "(no stderr)"
            raise CaskError(
                f"tle encryption failed (exit {r.returncode}): {stderr_msg}"
            )

        if not os.path.isfile(tmp_cask) or os.path.getsize(tmp_cask) == 0:
            raise CaskError("tle produced empty output — encryption failed silently")

        opslog.ok(
            f"Encryption output verified ({os.path.getsize(tmp_cask)} bytes)",
        )

        # Atomic replace: the verified new cask overwrites the old in place —
        # no displacement window where cask_path is missing, no .old backup
        # needed. repair_immutable at the top still heals any legacy crash
        # state. Cross-device tmp (CASK_WORK_DIR on a separate filesystem)
        # can't os.replace, so it falls back to a logged non-atomic copy.
        try:
            os.replace(tmp_cask, cask_path)
            opslog.ok(f"Cask credentials written to {cask_path}")
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise CaskError(f"cannot replace cask file: {e}") from e
            shutil.move(tmp_cask, cask_path)
            opslog.warn(
                f"Cask placed via non-atomic copy (work dir on a different "
                f"filesystem than {CASK_DIR})"
            )

        if os.geteuid() == 0:
            _ = os.chown(cask_path, 0, 0)
            _ = os.chown(CASK_DIR, 0, 0)
            opslog.ok(f"Cask directory ownership set to {MIKE.pw_name}:{MIKE.pw_name}")
        else:
            opslog.ok("Running as user — ownership unchanged")

        os.chmod(cask_path, 0o644)

        try:
            immutable_lib.set_immutable(cask_path)
            opslog.ok("Immutable flag set on cask credentials")
        except immutable_lib.ImmutableError as e:
            opslog.error(f"Immutable flag not set on cask credentials: {e}")
            opslog.warn("Repair re-applies the flag on the next cask/verify --fix run")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
