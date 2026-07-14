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

# ── Strict env lookup ────────────────────────────────────────────────────────
# Fails immediately if config.env wasn't sourced. No silent misconfiguration.

MIKE = pwd.getpwnam(os.environ["USERNAME"])
MIKE_UID = MIKE.pw_uid
MIKE_GID = MIKE.pw_gid
HOME_DIR = MIKE.pw_dir
SEAL_DIR = os.path.join(HOME_DIR, ".config", "seal")

# ── Adapter PATH resolution ──────────────────────────────────────────────────
# Ensure lockdown/lib/ is in PATH so seal/unseal find adapters by name.

LOCKDOWN_LIB = os.environ.get("LOCKDOWN_LIB_PATH", "/usr/local/lib/lockdown")
os.environ["PATH"] = f"{LOCKDOWN_LIB}:{os.environ['PATH']}"


# ── Path helpers ─────────────────────────────────────────────────────────────


def log_path(label):
    return os.path.join(SEAL_DIR, f"seal.{label}.log")


LOG_FILE = None
COMPONENT = None

SHELL_HISTORY_FILES = [".bash_history", ".zsh_history", ".zhistory"]

# ── Logging + Error handling ─────────────────────────────────────────────────


class SealError(Exception):
    pass


def ensure_seal_dir():
    os.makedirs(SEAL_DIR, exist_ok=True)
    os.chown(SEAL_DIR, MIKE_UID, MIKE_GID)


def log(component, msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {component}: {msg}"
    if not LOG_FILE:
        return
    ensure_seal_dir()
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def init_log(component, log_path, label, mode="w"):
    ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(log_path, mode) as f:
        f.write(f"[{ts}] {component}: [START] {label} started\n")
    log(component, f"[OK] Log initialized ({os.path.basename(log_path)})")


def step(component, name, fn, fatal=True):
    print(f"[*] {name}...")
    log(component, f"[STEP] {name}...")
    try:
        result = fn()
        log(component, f"[OK] {name}")
        return result
    except SealError:
        raise
    except Exception as e:
        msg = f"{name}: {e}"
        log(component, f"[ERROR] {msg}")
        if fatal:
            emergency_exit(component)
        else:
            log(component, f"[WARN] {name}: continuing despite failure")
            return None


def emergency_exit(component):
    log(component, "[ERROR] Aborting")
    log(component, "[END] FAILED")
    action = "Seal" if component == "seal" else "Unseal"
    print(f"\n[ERROR] {action} failed — see {LOG_FILE} for details", file=sys.stderr)
    sys.exit(1)


# ── Signal handling ──────────────────────────────────────────────────────────


def handle_signal(signum, frame):
    log(COMPONENT, f"[ERROR] Received signal {signum}, aborting")
    emergency_exit(COMPONENT)


signal.signal(signal.SIGTERM, handle_signal)


# ── Pre-flight gates ─────────────────────────────────────────────────────────


def gate_network():
    try:
        subprocess.run(
            ["timeout", "5", "getent", "hosts", "api.drand.sh"],
            capture_output=True,
            check=True,
        )
    except Exception:
        log(COMPONENT, "[WARN] Initial DNS check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(
                ["timeout", "5", "getent", "hosts", "api.drand.sh"],
                capture_output=True,
                check=True,
            )
        except Exception:
            raise SealError("DNS resolution failed (cannot resolve api.drand.sh).")

    try:
        subprocess.run(
            ["timeout", "5", "bash", "-c", "echo > /dev/tcp/api.drand.sh/443"],
            capture_output=True,
            check=True,
        )
    except Exception:
        log(COMPONENT, "[WARN] Initial TCP check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(
                ["timeout", "5", "bash", "-c", "echo > /dev/tcp/api.drand.sh/443"],
                capture_output=True,
                check=True,
            )
        except Exception:
            raise SealError("No internet connectivity (cannot reach api.drand.sh:443).")

    tle_candidates = ["/usr/local/bin/tle", os.path.join(HOME_DIR, "go", "bin", "tle")]
    tle_ok = False
    for tle_path in tle_candidates:
        if not (os.path.isfile(tle_path) and os.access(tle_path, os.X_OK)):
            continue
        try:
            r = subprocess.run(
                [tle_path, "--metadata"], capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0 and "chain_hash" in r.stdout:
                tle_ok = True
                break
        except Exception:
            continue

    if not tle_ok:
        log(COMPONENT, "[WARN] tle --metadata failed, retrying in 3s...")
        time.sleep(3)
        for tle_path in tle_candidates:
            if not (os.path.isfile(tle_path) and os.access(tle_path, os.X_OK)):
                continue
            try:
                r = subprocess.run(
                    [tle_path, "--metadata"], capture_output=True, text=True, timeout=30
                )
                if r.returncode == 0 and "chain_hash" in r.stdout:
                    tle_ok = True
                    break
            except Exception:
                continue

    if not tle_ok:
        raise SealError("tle cannot reach the drand timelock network.")


def gate_tle():
    candidates = ["/usr/local/bin/tle", os.path.join(HOME_DIR, "go", "bin", "tle")]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise SealError(f"tle not found at /usr/local/bin/tle or {HOME_DIR}/go/bin/tle")


def gate_cred_file(path, must_be_empty=False, exists_msg=None):
    if not os.path.isfile(path):
        msg = f"{path} not found.\n"
        if exists_msg:
            msg += exists_msg
        raise SealError(msg)
    size = os.path.getsize(path)
    if must_be_empty and size != 0:
        raise SealError(
            f"{path} must be empty.\n       Clear it with:\n         : > {path}"
        )
    if not must_be_empty and size == 0:
        raise SealError(f"{path} is empty")


# ── Discovery helpers ────────────────────────────────────────────────────────


def discover_session():
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
            log(COMPONENT, f"[WARN] discover_session: {e}")
            continue

    xdg_config_home = xdg_config_home or os.path.join(HOME_DIR, ".config")
    xdg_data_home = xdg_data_home or os.path.join(HOME_DIR, ".local", "share")
    return dbus_addr, wayland_display, xdg_data_home, xdg_config_home


# ── Clipboard ────────────────────────────────────────────────────────────────


def clear_clipboard(purge=False):
    log(COMPONENT, "[STEP] Clearing clipboard history...")

    # Simple clear: delegate to adapter (handles cliphist/wl-copy detection)
    if not purge:
        try:
            r = subprocess.run(
                ["clipboard-clear"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                log(COMPONENT, "[OK] Clipboard cleared via adapter")
            else:
                log(COMPONENT, f"[WARN] clipboard-clear failed: {r.stderr.strip()}")
        except Exception as e:
            log(COMPONENT, f"[WARN] clipboard-clear failed: {e}")
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
        )
        if r.returncode == 0:
            log(COMPONENT, "[OK] cliphist history wiped")
        else:
            log(COMPONENT, f"[WARN] cliphist wipe failed: {r.stderr.strip()}")
    except FileNotFoundError:
        log(COMPONENT, "[WARN] cliphist not installed — skipping")
    except Exception as e:
        log(COMPONENT, f"[WARN] cliphist wipe failed: {e}")

    # wl-copy --clear (fallback / belt-and-suspenders)
    try:
        r = subprocess.run(
            ["sudo", "-u", f"#{MIKE_UID}", "wl-copy", "--clear"],
            env=env,
            capture_output=True,
            timeout=10,
        )
        if r.returncode == 0:
            log(COMPONENT, "[OK] Wayland clipboard cleared")
        else:
            log(COMPONENT, f"[WARN] wl-copy --clear failed: {r.stderr.strip()}")
    except Exception as e:
        log(COMPONENT, f"[WARN] wl-copy --clear failed: {e}")


# ── Display ──────────────────────────────────────────────────────────────────


def prompt_manual_copy(label="password"):
    print(f"Select and copy the {label} above manually")


# ── Browser data clear ──────────────────────────────────────────────────────

# Assumes BrowserAddPersonEnabled=false enterprise policy prevents
# multi-profile creation — only the Default/ profile is targeted.
BROWSER_CONFIG_DIRS = [
    "BraveSoftware/Brave-Browser",
    "google-chrome",
    "chromium",
]

PROFILE_CLEANUP = [
    "Cookies", "Cookies-journal",
    "History", "History-journal",
    "Login Data", "Login Data-journal",
]

def clear_browser_data():
    log(COMPONENT, "[STEP] Clearing browser cache, cookies, history...")

    for subdir in BROWSER_CONFIG_DIRS:
        cache_path = os.path.join(HOME_DIR, ".cache", subdir, "Default")
        if os.path.isdir(cache_path):
            try:
                shutil.rmtree(cache_path, ignore_errors=True)
                log(COMPONENT, f"[OK] Removed {subdir}/Default cache")
            except Exception as e:
                log(COMPONENT, f"[WARN] Failed to remove {subdir} cache: {e}")

        profile_dir = os.path.join(HOME_DIR, ".config", subdir, "Default")
        if not os.path.isdir(profile_dir):
            continue
        for fname in PROFILE_CLEANUP:
            fpath = os.path.join(profile_dir, fname)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                    log(COMPONENT, f"[OK] Deleted {subdir}/Default/{fname}")
                except Exception as e:
                    log(COMPONENT, f"[WARN] Failed to delete {subdir}/{fname}: {e}")


# ── History wipe ─────────────────────────────────────────────────────────────


def wipe_history():
    log(COMPONENT, "[STEP] Wiping shell history...")
    wiped = 0
    for name in SHELL_HISTORY_FILES:
        path = os.path.join(HOME_DIR, name)
        if os.path.isfile(path):
            try:
                subprocess.run(["shred", "-u", path], capture_output=True, timeout=10)
                wiped += 1
            except Exception as e:
                log(COMPONENT, f"[WARN] Failed to wipe {name}: {e}")
    log(COMPONENT, f"[OK] Shell history wiped ({wiped} files)")


# ── Decrypt time check ────────────────────────────────────────────────────────

DRAND_CACHE = {}


def check_decrypt_time(tle_bin, sealed_path):
    if not os.path.isfile(sealed_path):
        raise SealError(f"Sealed file not found: {sealed_path}")

    r = subprocess.run(
        [tle_bin, "-d", "-o", "/dev/null", sealed_path],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if r.returncode == 0:
        return True

    match = re.search(r"round (\d+)", r.stderr)
    if not match:
        return True

    round_num = int(match.group(1))

    global DRAND_CACHE
    if not DRAND_CACHE:
        import json
        import urllib.request

        try:
            url = (
                "https://api.drand.sh/"
                "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971/info"
            )
            req = urllib.request.urlopen(url, timeout=10)
            info = json.loads(req.read())
            DRAND_CACHE["genesis"] = info["genesis_time"]
            DRAND_CACHE["period"] = info["period"]
        except Exception as e:
            log(COMPONENT, f"[WARN] Failed to fetch drand chain info: {e}")
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

    print("")
    print("============================================")
    print("  Timelock has NOT expired yet")
    print("============================================")
    print("")
    print(f"  Will be available at: {unlock_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  ({remaining} from now)")
    print("")
    print("  Wait for the timelock to expire, then run unseal again.")
    print("")
    return False


# ── Reboot ───────────────────────────────────────────────────────────────────


def reboot():
    log(COMPONENT, "[STEP] Rebooting in 6 seconds...")
    print("")
    print("============================================")
    print("  Rebooting in 6 seconds...")
    print("============================================")
    print("")
    time.sleep(6)
    log(COMPONENT, "[STEP] Rebooting...")
    try:
        subprocess.run(["sudo", "/sbin/reboot", "-f"], timeout=5)
    except Exception as e:
        log(COMPONENT, f"[ERROR] reboot failed: {e}")
        log(COMPONENT, "[ERROR] Please reboot manually")
        log(COMPONENT, "[END] FAILED")
        sys.exit(1)


# ── Interactive prompts ──────────────────────────────────────────────────────


def compute_expiry(duration):
    human = (
        duration.replace("m", " minutes").replace("h", " hours").replace("d", " days")
    )
    try:
        r = subprocess.run(
            ["date", "-u", "-d", f"+{human}", "+%Y-%m-%d %H:%M:%S UTC"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip()
        log(
            COMPONENT,
            f"[WARN] date command failed (exit {r.returncode}) for duration '{duration}'",
        )
    except Exception as e:
        log(COMPONENT, f"[WARN] date command raised: {e}")
    return "(unknown)"


def prompt_duration():
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
        log(COMPONENT, "[END] seal cancelled")
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
            log(COMPONENT, "[END] seal cancelled")
            sys.exit(0)
        if re.match(r"^\d+[mhd]$", dur):
            return dur
        print("Error: invalid format. Use e.g. 30m, 4h, 7d", file=sys.stderr)
        log(COMPONENT, "[END] seal failed — invalid input")
        sys.exit(1)

    if not choice:
        print("Cancelled.")
        log(COMPONENT, "[END] seal cancelled")
        sys.exit(0)
    else:
        print("Invalid choice", file=sys.stderr)
        log(COMPONENT, "[END] seal failed — invalid input")
        sys.exit(1)


def confirm(label, cred_path, duration, expiry, items):
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
        log(COMPONENT, "[END] seal cancelled")
        sys.exit(0)
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        log(COMPONENT, "[END] seal cancelled")
        sys.exit(0)


# ── File sanitization ────────────────────────────────────────────────────────


def shred_file(path):
    if not os.path.exists(path):
        return
    try:
        size = os.path.getsize(path)
        with open(path, "wb") as f:
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        log(COMPONENT, f"[WARN] Failed to overwrite {path}: {e}")

    try:
        subprocess.run(
            ["shred", "-u", path], capture_output=True, check=True, timeout=30
        )
    except Exception as e:
        log(COMPONENT, f"[WARN] shred failed: {e}, attempting rm -f")
        subprocess.run(["rm", "-f", path], capture_output=True)

    if os.path.exists(path):
        raise SealError(f"Failed to shred {path} — file still exists")


# ── Encryption ───────────────────────────────────────────────────────────────


def encrypt(tle_bin, cred_path, sealed_path, duration):
    log(COMPONENT, "[STEP] Preparing encryption...")

    if os.path.exists(sealed_path):
        subprocess.run(
            ["sudo", "chattr", "-i", sealed_path], capture_output=True, timeout=10
        )
        os.remove(sealed_path)

    tmpdir = tempfile.mkdtemp(prefix="seal_", dir=SEAL_DIR)
    try:
        tmp_cred = os.path.join(tmpdir, "credentials")
        shutil.copy2(cred_path, tmp_cred)

        tmp_sealed = os.path.join(tmpdir, "sealed")
        log(COMPONENT, f"[STEP] Running tle -e -D {duration}...")
        try:
            r = subprocess.run(
                [tle_bin, "-e", "-D", duration, "--armor", "-o", tmp_sealed, tmp_cred],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            raise SealError("tle encryption timed out after 300 seconds")

        if r.returncode != 0:
            stderr_msg = r.stderr.strip() if r.stderr.strip() else "(no stderr)"
            raise SealError(
                f"tle encryption failed (exit {r.returncode}): {stderr_msg}"
            )

        if not os.path.isfile(tmp_sealed) or os.path.getsize(tmp_sealed) == 0:
            raise SealError("tle produced empty output — encryption failed silently")

        log(
            COMPONENT,
            f"[OK] Encryption output verified ({os.path.getsize(tmp_sealed)} bytes)",
        )

        shutil.move(tmp_sealed, sealed_path)
        log(COMPONENT, f"[OK] Sealed credentials written to {sealed_path}")

        if os.geteuid() == 0:
            os.chown(sealed_path, MIKE_UID, MIKE_GID)
            os.chown(SEAL_DIR, MIKE_UID, MIKE_GID)
            log(COMPONENT, f"[OK] Seal directory ownership set to {MIKE.pw_name}:{MIKE.pw_name}")
        else:
            log(COMPONENT, "[OK] Running as user — ownership unchanged")

        os.chmod(sealed_path, 0o644)

        try:
            subprocess.run(
                ["sudo", "chattr", "+i", sealed_path], capture_output=True, check=True
            )
            log(COMPONENT, "[OK] Immutable flag set on sealed credentials")
        except Exception as e:
            log(
                COMPONENT,
                f"[WARN] chattr +i failed: {e} — file not protected (non-fatal)",
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
