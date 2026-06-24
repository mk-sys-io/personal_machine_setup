#!/usr/bin/python3
"""Shared library for seal and unseal operations."""

import glob
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


# ── Constants ────────────────────────────────────────────────────────────────

MIKE = pwd.getpwnam("mike")
MIKE_UID = MIKE.pw_uid
MIKE_GID = MIKE.pw_gid
HOME_DIR = MIKE.pw_dir
SEAL_DIR = os.path.join(HOME_DIR, ".config", "seal")

LOG_FILE = None
COMPONENT = None

SHELL_HISTORY_FILES = [".bash_history", ".zsh_history", ".zhistory"]

# ── Logging + Error handling ─────────────────────────────────────────────────

class SealError(Exception):
    pass


def _ensure_seal_dir():
    os.makedirs(SEAL_DIR, exist_ok=True)
    os.chown(SEAL_DIR, MIKE_UID, MIKE_GID)


def _log(component, msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {component}: {msg}"
    if not LOG_FILE:
        return
    _ensure_seal_dir()
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def _init_log(component, log_path, label, mode="w"):
    _ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(log_path, mode) as f:
        f.write(f"[{ts}] {component}: [START] {label} started\n")
    _log(component, f"[OK] Log initialized ({os.path.basename(log_path)})")


def _step(component, name, fn, fatal=True):
    print(f"[*] {name}...")
    _log(component, f"[STEP] {name}...")
    try:
        result = fn()
        _log(component, f"[OK] {name}")
        return result
    except SealError:
        raise
    except Exception as e:
        msg = f"{name}: {e}"
        _log(component, f"[ERROR] {msg}")
        if fatal:
            _emergency_exit(component)
        else:
            _log(component, f"[WARN] {name}: continuing despite failure")
            return None


def _emergency_exit(component):
    _log(component, "[ERROR] Aborting")
    _log(component, "[END] FAILED")
    action = "Seal" if component == "seal" else "Unseal"
    print(f"\n[ERROR] {action} failed — see {LOG_FILE} for details", file=sys.stderr)
    sys.exit(1)


# ── Signal handling ──────────────────────────────────────────────────────────

def _handle_signal(signum, frame):
    _log(COMPONENT, f"[ERROR] Received signal {signum}, aborting")
    _emergency_exit(COMPONENT)


signal.signal(signal.SIGTERM, _handle_signal)


# ── Pre-flight gates ─────────────────────────────────────────────────────────

def _gate_network():
    try:
        subprocess.run(["timeout", "5", "getent", "hosts", "api.drand.sh"],
                       capture_output=True, check=True)
    except Exception:
        _log(COMPONENT, "[WARN] Initial DNS check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(["timeout", "5", "getent", "hosts", "api.drand.sh"],
                           capture_output=True, check=True)
        except Exception:
            raise SealError("DNS resolution failed (cannot resolve api.drand.sh).")

    try:
        subprocess.run(["timeout", "5", "bash", "-c",
                        "echo > /dev/tcp/api.drand.sh/443"],
                       capture_output=True, check=True)
    except Exception:
        _log(COMPONENT, "[WARN] Initial TCP check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(["timeout", "5", "bash", "-c",
                            "echo > /dev/tcp/api.drand.sh/443"],
                           capture_output=True, check=True)
        except Exception:
            raise SealError("No internet connectivity (cannot reach api.drand.sh:443).")

    tle_candidates = ["/usr/local/bin/tle", os.path.join(HOME_DIR, "go", "bin", "tle")]
    tle_ok = False
    for tle_path in tle_candidates:
        if not (os.path.isfile(tle_path) and os.access(tle_path, os.X_OK)):
            continue
        try:
            r = subprocess.run(
                [tle_path, "--metadata"],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0 and "chain_hash" in r.stdout:
                tle_ok = True
                break
        except Exception:
            continue

    if not tle_ok:
        _log(COMPONENT, "[WARN] tle --metadata failed, retrying in 3s...")
        time.sleep(3)
        for tle_path in tle_candidates:
            if not (os.path.isfile(tle_path) and os.access(tle_path, os.X_OK)):
                continue
            try:
                r = subprocess.run(
                    [tle_path, "--metadata"],
                    capture_output=True, text=True, timeout=30
                )
                if r.returncode == 0 and "chain_hash" in r.stdout:
                    tle_ok = True
                    break
            except Exception:
                continue

    if not tle_ok:
        raise SealError("tle cannot reach the drand timelock network.")


def _gate_tle():
    candidates = ["/usr/local/bin/tle", os.path.join(HOME_DIR, "go", "bin", "tle")]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise SealError(
        f"tle not found at /usr/local/bin/tle or {HOME_DIR}/go/bin/tle"
    )


def _gate_cred_file(path, must_be_empty=False, exists_msg=None):
    if not os.path.isfile(path):
        msg = f"{path} not found.\n"
        if exists_msg:
            msg += exists_msg
        raise SealError(msg)
    size = os.path.getsize(path)
    if must_be_empty and size != 0:
        raise SealError(
            f"{path} must be empty.\n"
            f"       Clear it with:\n"
            f"         : > {path}"
        )
    if not must_be_empty and size == 0:
        raise SealError(f"{path} is empty")


# ── Discovery helpers ────────────────────────────────────────────────────────

def _discover_session():
    dbus_addr = ""
    wayland_display = ""
    xdg_data_home = ""
    xdg_config_home = ""

    for proc in ["sway", "waybar", "copyq"]:
        try:
            r = subprocess.run(
                ["pgrep", "-u", str(MIKE_UID), "-x", proc],
                capture_output=True, text=True, timeout=5
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
        except Exception as e:
            _log(COMPONENT, f"[WARN] _discover_session: {e}")
            continue

    xdg_config_home = xdg_config_home or os.path.join(HOME_DIR, ".config")
    xdg_data_home = xdg_data_home or os.path.join(HOME_DIR, ".local", "share")
    return dbus_addr, wayland_display, xdg_data_home, xdg_config_home


# ── Clipboard ────────────────────────────────────────────────────────────────

def _clear_clipboard(purge=False):
    _log(COMPONENT, "[STEP] Clearing clipboard history...")
    dbus_addr, wayland_display, xdg_data_home, xdg_config_home = _discover_session()
    xdg_runtime = f"/run/user/{MIKE_UID}"

    base_env = {"XDG_RUNTIME_DIR": xdg_runtime}

    if not wayland_display:
        _log(COMPONENT, "[WARN] WAYLAND_DISPLAY unknown — skipping wl-copy")
    else:
        env = {**base_env, "WAYLAND_DISPLAY": wayland_display}
        try:
            r = subprocess.run(
                ["sudo", "-u", f"#{MIKE_UID}", "wl-copy", "--clear"],
                env=env, capture_output=True, timeout=10
            )
            if r.returncode == 0:
                _log(COMPONENT, "[OK] Wayland clipboard cleared")
            else:
                _log(COMPONENT, f"[WARN] wl-copy --clear failed: {r.stderr.strip()}")
        except Exception as e:
            _log(COMPONENT, f"[WARN] wl-copy --clear failed: {e}")

    if not dbus_addr:
        _log(COMPONENT, "[WARN] DBUS_SESSION_BUS_ADDRESS unknown — skipping copyq clear")
    else:
        env = {**base_env, "DBUS_SESSION_BUS_ADDRESS": dbus_addr}
        try:
            r = subprocess.run(
                ["sudo", "-u", f"#{MIKE_UID}", "copyq", "clear"],
                env=env, capture_output=True, timeout=10
            )
            if r.returncode == 0:
                _log(COMPONENT, "[OK] CopyQ history cleared via D-Bus")
            else:
                _log(COMPONENT, f"[WARN] copyq clear failed: {r.stderr.strip()}")
        except Exception as e:
            _log(COMPONENT, f"[WARN] copyq clear failed: {e}")

    if purge:
        try:
            r = subprocess.run(
                ["pgrep", "-u", str(MIKE_UID), "-x", "copyq"],
                capture_output=True, text=True, timeout=5
            )
            if r.stdout.strip():
                subprocess.run(["pkill", "-u", str(MIKE_UID), "copyq"],
                               capture_output=True, timeout=5)
                time.sleep(0.2)
                for _ in range(5):
                    r2 = subprocess.run(
                        ["pgrep", "-u", str(MIKE_UID), "-x", "copyq"],
                        capture_output=True, text=True, timeout=5
                    )
                    if not r2.stdout.strip():
                        break
                    time.sleep(0.2)
                else:
                    subprocess.run(["pkill", "-9", "-u", str(MIKE_UID), "copyq"],
                                   capture_output=True, timeout=5)
                    _log(COMPONENT, "[WARN] copyq force-killed (SIGKILL)")
        except Exception as e:
            _log(COMPONENT, f"[WARN] Failed to terminate copyq: {e}")

        config_copyq = os.path.join(xdg_config_home, "copyq")
        if os.path.isdir(config_copyq):
            for pattern in ["copyq_tab_*.dat", "copyq_tabs.ini", "copyq.lock"]:
                for f in glob.glob(os.path.join(config_copyq, pattern)):
                    try:
                        os.remove(f)
                    except Exception as e:
                        _log(COMPONENT, f"[WARN] Failed to remove {f}: {e}")

        data_copyq = os.path.join(xdg_data_home, "copyq")
        if os.path.isdir(data_copyq):
            shutil.rmtree(data_copyq, ignore_errors=True)


def copy_password(password, label):
    try:
        r = subprocess.run(
            ["copyq", "copy"], input=password,
            capture_output=True, timeout=10
        )
        if r.returncode == 0:
            _log(COMPONENT, f"[OK] {label} copied to clipboard via copyq")
            return "copyq"
    except Exception as e:
        _log(COMPONENT, f"[WARN] copyq copy failed: {e}")

    try:
        r = subprocess.run(
            ["wl-copy"], input=password,
            capture_output=True, timeout=10
        )
        if r.returncode == 0:
            _log(COMPONENT, f"[OK] {label} copied to clipboard via wl-copy")
            return "wl-copy"
    except Exception as e:
        _log(COMPONENT, f"[WARN] wl-copy failed: {e}")

    return None


# ── History wipe ─────────────────────────────────────────────────────────────

def _wipe_history():
    _log(COMPONENT, "[STEP] Wiping shell history...")
    wiped = 0
    for name in SHELL_HISTORY_FILES:
        path = os.path.join(HOME_DIR, name)
        if os.path.isfile(path):
            try:
                subprocess.run(["shred", "-u", path], capture_output=True, timeout=10)
                wiped += 1
            except Exception as e:
                _log(COMPONENT, f"[WARN] Failed to wipe {name}: {e}")
    _log(COMPONENT, f"[OK] Shell history wiped ({wiped} files)")


# ── Reboot ───────────────────────────────────────────────────────────────────

def _reboot():
    _log(COMPONENT, "[STEP] Rebooting in 10 seconds...")
    print("")
    print("============================================")
    print("  Rebooting in 10 seconds...")
    print("============================================")
    print("")
    time.sleep(10)
    _log(COMPONENT, "[STEP] Rebooting...")
    try:
        subprocess.run(["reboot", "-f"], timeout=5)
    except Exception as e:
        _log(COMPONENT, f"[ERROR] reboot failed: {e}")
        _log(COMPONENT, "[ERROR] Please reboot manually")
        _log(COMPONENT, "[END] FAILED")
        sys.exit(1)


# ── Interactive prompts ──────────────────────────────────────────────────────

def _compute_expiry(duration):
    human = duration.replace("m", " minutes").replace("h", " hours").replace("d", " days")
    try:
        r = subprocess.run(
            ["date", "-u", "-d", f"+{human}", "+%Y-%m-%d %H:%M:%S UTC"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return r.stdout.strip()
        _log(COMPONENT, f"[WARN] date command failed (exit {r.returncode}) for duration '{duration}'")
    except Exception as e:
        _log(COMPONENT, f"[WARN] date command raised: {e}")
    return "(unknown)"


def _prompt_duration():
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
        _log(COMPONENT, "[END] seal cancelled")
        sys.exit(0)

    durations = {"1": "30m", "2": "1h", "3": "3h", "4": "1d", "5": "3d", "6": "7d"}
    if choice in durations:
        return durations[choice]

    if choice == "7":
        try:
            dur = input("Enter duration (e.g. 30m, 4h, 7d): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            _log(COMPONENT, "[END] seal cancelled")
            sys.exit(0)
        if re.match(r"^\d+[mhd]$", dur):
            return dur
        print("Error: invalid format. Use e.g. 30m, 4h, 7d", file=sys.stderr)
        _log(COMPONENT, "[END] seal failed — invalid input")
        sys.exit(1)

    if not choice:
        print("Cancelled.")
        _log(COMPONENT, "[END] seal cancelled")
        sys.exit(0)
    else:
        print("Invalid choice", file=sys.stderr)
        _log(COMPONENT, "[END] seal failed — invalid input")
        sys.exit(1)


def _confirm(label, cred_path, duration, expiry, items):
    print("")
    print("=============================================")
    print(f"  You are about to seal the {label}.")
    print("=============================================")
    print("")
    print(f"  Timelock:     {duration}")
    print(f"  Expires:      {expiry}")
    print(f"  Credentials:  {cred_path}")
    print("")
    print("  This will:")
    for item in items:
        print(f"    - {item}")
    print("")
    try:
        confirm = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        _log(COMPONENT, "[END] seal cancelled")
        sys.exit(0)
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        _log(COMPONENT, "[END] seal cancelled")
        sys.exit(0)


# ── Encryption ───────────────────────────────────────────────────────────────

def _encrypt(tle_bin, cred_path, sealed_path, duration):
    _log(COMPONENT, "[STEP] Preparing encryption...")

    subprocess.run(["chattr", "-i", sealed_path], capture_output=True, timeout=10)
    if os.path.exists(sealed_path):
        os.remove(sealed_path)

    tmpdir = tempfile.mkdtemp(prefix="seal_", dir=SEAL_DIR)
    try:
        tmp_cred = os.path.join(tmpdir, "credentials")
        shutil.copy2(cred_path, tmp_cred)

        tmp_sealed = os.path.join(tmpdir, "sealed")
        _log(COMPONENT, f"[STEP] Running tle -e -D {duration}...")
        try:
            r = subprocess.run(
                [tle_bin, "-e", "-D", duration, "--armor", "-o", tmp_sealed, tmp_cred],
                capture_output=True, text=True, timeout=300
            )
        except subprocess.TimeoutExpired:
            raise SealError("tle encryption timed out after 300 seconds")

        if r.returncode != 0:
            stderr_msg = r.stderr.strip() if r.stderr.strip() else "(no stderr)"
            raise SealError(f"tle encryption failed (exit {r.returncode}): {stderr_msg}")

        if not os.path.isfile(tmp_sealed) or os.path.getsize(tmp_sealed) == 0:
            raise SealError("tle produced empty output — encryption failed silently")

        _log(COMPONENT, f"[OK] Encryption output verified ({os.path.getsize(tmp_sealed)} bytes)")

        shutil.move(tmp_sealed, sealed_path)
        _log(COMPONENT, f"[OK] Sealed credentials written to {sealed_path}")

        os.chown(sealed_path, MIKE_UID, MIKE_GID)
        os.chmod(sealed_path, 0o644)

        os.chown(SEAL_DIR, MIKE_UID, MIKE_GID)
        _log(COMPONENT, "[OK] Seal directory ownership set to mike:mike")

        try:
            subprocess.run(["chattr", "+i", sealed_path], capture_output=True, check=True)
            _log(COMPONENT, "[OK] Immutable flag set on sealed credentials")
        except Exception as e:
            _log(COMPONENT, f"[WARN] chattr +i failed: {e} — file not protected (non-fatal)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
