#!/usr/bin/python3
"""Shared library for cask and uncask operations."""

from __future__ import annotations

import http.client
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import NoReturn

import opslog

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
    "cask-mobile": "Cask mobile",
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


signal.signal(signal.SIGTERM, handle_signal)


# ── Pre-flight gates ─────────────────────────────────────────────────────────


def gate_network() -> None:
    try:
        subprocess.run(
            ["timeout", "5", "getent", "hosts", "{{ .Env.DRAND_HOST }}"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        opslog.warn("Initial DNS check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(
                ["timeout", "5", "getent", "hosts", "{{ .Env.DRAND_HOST }}"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            raise CaskError("DNS resolution failed (cannot resolve {{ .Env.DRAND_HOST }}).")

    try:
        subprocess.run(
            ["timeout", "5", "bash", "-c", "echo > /dev/tcp/{{ .Env.DRAND_HOST }}/443"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        opslog.warn("Initial TCP check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(
                ["timeout", "5", "bash", "-c", "echo > /dev/tcp/{{ .Env.DRAND_HOST }}/443"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            raise CaskError("No internet connectivity (cannot reach {{ .Env.DRAND_HOST }}:443).")

    tle_candidates: list[str] = ["{{ .Env.TLE_PRIMARY_PATH }}", "{{ .Env.TLE_FALLBACK_PATH }}"]
    tle_ok = False
    for tle_path in tle_candidates:
        if not (os.path.isfile(tle_path) and os.access(tle_path, os.X_OK)):
            continue
        try:
            r = subprocess.run(
                [tle_path, "--metadata"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if r.returncode == 0 and "chain_hash" in r.stdout:
                tle_ok = True
                break
        except (subprocess.TimeoutExpired, OSError) as e:
            opslog.debug(f"tle --metadata probe failed: {e}")
            continue

    if not tle_ok:
        opslog.warn("tle --metadata failed, retrying in 3s...")
        time.sleep(3)
        for tle_path in tle_candidates:
            if not (os.path.isfile(tle_path) and os.access(tle_path, os.X_OK)):
                continue
            try:
                r = subprocess.run(
                    [tle_path, "--metadata"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if r.returncode == 0 and "chain_hash" in r.stdout:
                    tle_ok = True
                    break
            except (subprocess.TimeoutExpired, OSError) as e:
                opslog.debug(f"tle --metadata probe failed: {e}")
                continue

    if not tle_ok:
        raise CaskError("tle cannot reach the drand timelock network.")


def gate_tle() -> str:
    candidates: list[str] = ["{{ .Env.TLE_PRIMARY_PATH }}", "{{ .Env.TLE_FALLBACK_PATH }}"]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise CaskError("tle not found at {{ .Env.TLE_PRIMARY_PATH }} or {{ .Env.TLE_FALLBACK_PATH }}")


def gate_cred_file(path: str, must_be_empty: bool = False, exists_msg: str | None = None) -> None:
    if not os.path.isfile(path):
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


# ── Discovery helpers ────────────────────────────────────────────────────────


def discover_session() -> tuple[str, str, str, str]:
    dbus_addr = ""
    wayland_display = ""
    xdg_data_home = ""
    xdg_config_home = ""

    for proc in ["sway", "waybar"]:
        try:
            r = subprocess.run(
                ["pgrep", "-u", str(MIKE_UID), "-x", proc],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if not r.stdout.strip():
                continue
            pid = r.stdout.strip().split("\n")[0]
            env_path = f"/proc/{pid}/environ"
            if not os.path.isfile(env_path) or not os.access(env_path, os.R_OK):
                continue
            with open(env_path, "rb") as f:
                raw = f.read()
            for entry in raw.split(b"\0"):
                if not entry:
                    continue
                try:
                    dec = entry.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if dec.startswith("DBUS_SESSION_BUS_ADDRESS="):
                    dbus_addr = dec.split("=", 1)[1]
                elif dec.startswith("WAYLAND_DISPLAY="):
                    wayland_display = dec.split("=", 1)[1]
                elif dec.startswith("XDG_DATA_HOME="):
                    xdg_data_home = dec.split("=", 1)[1]
                elif dec.startswith("XDG_CONFIG_HOME="):
                    xdg_config_home = dec.split("=", 1)[1]
            if dbus_addr:
                break
        except (subprocess.TimeoutExpired, OSError) as e:
            opslog.warn(f"discover_session: {e}")
            continue

    xdg_config_home = xdg_config_home or os.path.join(HOME_DIR, ".config")
    xdg_data_home = xdg_data_home or os.path.join(HOME_DIR, ".local", "share")
    return dbus_addr, wayland_display, xdg_data_home, xdg_config_home


# ── Clipboard ────────────────────────────────────────────────────────────────


def clear_clipboard(purge: bool = False) -> None:
    opslog.set_step("Clearing clipboard history")

    # Simple clear: delegate to adapter (handles cliphist/wl-copy detection)
    if not purge:
        try:
            r = subprocess.run(
                ["clipboard-clear"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if r.returncode == 0:
                opslog.ok("Clipboard cleared via adapter")
            else:
                opslog.warn(f"clipboard-clear failed: {r.stderr.strip()}")
        except (subprocess.TimeoutExpired, OSError) as e:
            opslog.warn(f"clipboard-clear failed: {e}")
        return

    # Purge mode: run cliphist wipe + wl-copy --clear as user with Wayland env
    _, wayland_display, _, _ = discover_session()
    xdg_runtime = f"/run/user/{MIKE_UID}"

    env = {"XDG_RUNTIME_DIR": xdg_runtime}
    if wayland_display:
        env["WAYLAND_DISPLAY"] = wayland_display

    # cliphist wipe (primary)
    try:
        r = subprocess.run(
            ["sudo", "-u", f"#{MIKE_UID}", "cliphist", "wipe"],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if r.returncode == 0:
            opslog.ok("cliphist history wiped")
        else:
            opslog.warn(f"cliphist wipe failed: {r.stderr.strip()}")
    except FileNotFoundError:
        opslog.warn("cliphist not installed — skipping")
    except (subprocess.TimeoutExpired, OSError) as e:
        opslog.warn(f"cliphist wipe failed: {e}")

    # wl-copy --clear (fallback / belt-and-suspenders)
    try:
        r = subprocess.run(
            ["sudo", "-u", f"#{MIKE_UID}", "wl-copy", "--clear"],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if r.returncode == 0:
            opslog.ok("Wayland clipboard cleared")
        else:
            opslog.warn(f"wl-copy --clear failed: {r.stderr.strip()}")
    except (subprocess.TimeoutExpired, OSError) as e:
        opslog.warn(f"wl-copy --clear failed: {e}")


# ── Display ──────────────────────────────────────────────────────────────────


def prompt_manual_copy(label: str = "password") -> None:
    print(f"Select and copy the {label} above manually")


# ── Browser data clear ──────────────────────────────────────────────────────

# Assumes BrowserAddPersonEnabled=false enterprise policy prevents
# multi-profile creation — only the Default/ profile is targeted.
# ruff: ignore[SIM905] -- rendered config value, not a literal; split() needed at runtime
BROWSER_CONFIG_DIRS: Sequence[str] = "{{ .Env.BROWSER_CONFIG_DIRS }}".split()

PROFILE_CLEANUP: list[str] = [
    "Cookies", "Cookies-journal",
    "History", "History-journal",
    "Login Data", "Login Data-journal",
]


def clear_browser_data() -> None:
    opslog.set_step("Clearing browser cache, cookies, history")

    for subdir in BROWSER_CONFIG_DIRS:
        cache_path = os.path.join(HOME_DIR, ".cache", subdir, "Default")
        if os.path.isdir(cache_path):
            try:
                shutil.rmtree(cache_path, ignore_errors=True)
                opslog.ok(f"Removed {subdir}/Default cache")
            except OSError as e:
                opslog.warn(f"Failed to remove {subdir} cache: {e}")

        profile_dir = os.path.join(HOME_DIR, ".config", subdir, "Default")
        if not os.path.isdir(profile_dir):
            continue
        for fname in PROFILE_CLEANUP:
            fpath = os.path.join(profile_dir, fname)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                    opslog.ok(f"Deleted {subdir}/Default/{fname}")
                except OSError as e:
                    opslog.warn(f"Failed to delete {subdir}/{fname}: {e}")


# ── History wipe ─────────────────────────────────────────────────────────────


def wipe_history() -> None:
    opslog.set_step("Wiping shell history")
    wiped = 0
    for name in SHELL_HISTORY_FILES:
        path = os.path.join(HOME_DIR, name)
        if os.path.isfile(path):
            try:
                subprocess.run(
                    ["shred", "-u", path], capture_output=True, timeout=10, check=False
                )
                wiped += 1
            except (subprocess.TimeoutExpired, OSError) as e:
                opslog.warn(f"Failed to wipe {name}: {e}")
    opslog.ok(f"Shell history wiped ({wiped} files)")


# ── Decrypt time check ────────────────────────────────────────────────────────

DRAND_CACHE: dict[str, int] = {}


def check_decrypt_time(tle_bin: str, cask_path: str) -> bool:
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

    if not DRAND_CACHE:
        import json
        import urllib.request

        try:
            url = (
                "https://{{ .Env.DRAND_HOST }}/"
                "{{ .Env.DRAND_CHAIN_HASH }}/info"
            )
            req = urllib.request.urlopen(url, timeout=10)
            info = json.loads(req.read())
            DRAND_CACHE["genesis"] = info["genesis_time"]
            DRAND_CACHE["period"] = info["period"]
        except (OSError, ValueError, KeyError, http.client.HTTPException) as e:
            opslog.warn(f"Failed to fetch drand chain info: {e}")
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
    import json
    import urllib.request

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

    if not DRAND_CACHE:
        try:
            url = "https://{{ .Env.DRAND_HOST }}/{{ .Env.DRAND_CHAIN_HASH }}/info"
            req = urllib.request.urlopen(url, timeout=10)
            info = json.loads(req.read())
            DRAND_CACHE["genesis"] = info["genesis_time"]
            DRAND_CACHE["period"] = info["period"]
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            meta_path = os.path.join(CASK_DIR, "metadata.json")
            if os.path.isfile(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
                cask_ts = meta["cask_timestamp"]
                dur_str = meta["tle_duration"]
                dur_secs = parse_duration(dur_str)
                elapsed = int(time.time()) - cask_ts
                remaining = dur_secs - elapsed
                return max(remaining, 0)
            raise CaskError("Cannot determine remaining TLE time (drand unreachable, no metadata)")

    unlock_ts = DRAND_CACHE["genesis"] + (round_num - 1) * DRAND_CACHE["period"]
    now = int(time.time())
    remaining = unlock_ts - now
    return max(remaining, 0)


def store_cask_metadata(duration: str) -> None:
    import json
    meta = {"cask_timestamp": int(time.time()), "tle_duration": duration}
    path = os.path.join(CASK_DIR, "metadata.json")
    tmp = path + ".new"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, path)
    os.chown(path, 0, 0)
    os.chmod(path, 0o644)
    try:
        subprocess.run(["chattr", "+i", path], capture_output=True, check=False)
    except OSError as e:
        opslog.warn(f"Failed to make metadata immutable: {e}")


# ── Reboot ───────────────────────────────────────────────────────────────────


def reboot() -> None:
    opslog.set_step("Rebooting in 6 seconds")
    print()
    print("============================================")
    print("  Rebooting in 6 seconds...")
    print("============================================")
    print()
    time.sleep(6)
    opslog.set_step("Rebooting")
    try:
        subprocess.run(["sudo", "/sbin/reboot", "-f"], timeout=5, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        opslog.error(f"reboot failed: {e}")
        opslog.error("Please reboot manually")
        opslog.end_session(_signal_component, "FAILED")
        sys.exit(1)


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
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        opslog.warn(f"Failed to overwrite {path}: {e}")

    try:
        subprocess.run(
            ["shred", "-u", path], capture_output=True, check=True, timeout=30
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        opslog.warn(f"shred failed: {e}, attempting rm -f")
        subprocess.run(["rm", "-f", path], capture_output=True, check=False)

    if os.path.exists(path):
        raise CaskError(f"Failed to shred {path} — file still exists")


# ── Encryption ───────────────────────────────────────────────────────────────


def encrypt(tle_bin: str, cred_path: str, cask_path: str, duration: str) -> None:
    opslog.set_step("Preparing encryption")

    # chattr -i old cask (don't delete yet — keep as backup)
    if os.path.exists(cask_path):
        subprocess.run(
            ["sudo", "chattr", "-i", cask_path], capture_output=True, timeout=10, check=False
        )

    tmpdir = tempfile.mkdtemp(prefix="cask_", dir=CASK_WORK_DIR)
    try:
        tmp_cred = os.path.join(tmpdir, "credentials")
        shutil.copy2(cred_path, tmp_cred)

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
            raise CaskError("tle encryption timed out after {{ .Env.TLE_TIMEOUT }} seconds")

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

        # Delete old cask file only after new one is verified
        if os.path.exists(cask_path):
            os.remove(cask_path)
            opslog.ok("Old cask file removed")

        shutil.move(tmp_cask, cask_path)
        opslog.ok(f"Cask credentials written to {cask_path}")

        if os.geteuid() == 0:
            os.chown(cask_path, 0, 0)
            os.chown(CASK_DIR, 0, 0)
            opslog.ok(f"Cask directory ownership set to {MIKE.pw_name}:{MIKE.pw_name}")
        else:
            opslog.ok("Running as user — ownership unchanged")

        os.chmod(cask_path, 0o644)

        try:
            subprocess.run(
                ["sudo", "chattr", "+i", cask_path], capture_output=True, check=True
            )
            opslog.ok("Immutable flag set on cask credentials")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            opslog.warn(
                f"chattr +i failed: {e} — file not protected (non-fatal)",
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
