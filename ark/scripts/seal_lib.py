#!/usr/bin/python3
"""Shared library for seal and unseal operations."""

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

MIKE = pwd.getpwnam("{{ .Env.USERNAME }}")
MIKE_UID = MIKE.pw_uid
MIKE_GID = MIKE.pw_gid
HOME_DIR = MIKE.pw_dir
SEAL_DIR = "{{ .Env.ARK_DATA_PATH }}/seal"                              # sealed files (root-owned)
SEAL_WORK_DIR = os.path.join(HOME_DIR, ".local", "share", "seal")  # working dir (user-owned)
MODE_FILE = "{{ .Env.ARK_DATA_PATH }}/mode"

# ── Adapter PATH resolution ──────────────────────────────────────────────────
# Ensure etc/ark/adapters/ is in PATH so seal/unseal find adapters by name.

ARK_LIB = os.environ.get("ARK_LIB_PATH", "/usr/local/lib/ark")
os.environ["PATH"] = f"{ARK_LIB}:{os.environ['PATH']}"


# ── Path helpers ─────────────────────────────────────────────────────────────


def log_path(label):
    return os.path.join(SEAL_WORK_DIR, f"seal.{label}.log")


LOG_FILE = None
COMPONENT = None

SHELL_HISTORY_FILES = "{{ .Env.SHELL_HISTORY_FILES }}".split()

# ── Logging + Error handling ─────────────────────────────────────────────────


class SealError(Exception):
    pass


def ensure_seal_dir():
    os.makedirs(SEAL_DIR, exist_ok=True)
    os.chown(SEAL_DIR, 0, 0)
    os.chmod(SEAL_DIR, 0o750)
    os.makedirs(SEAL_WORK_DIR, exist_ok=True)
    os.chown(SEAL_WORK_DIR, MIKE_UID, MIKE_GID)


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
            ["timeout", "5", "getent", "hosts", "{{ .Env.DRAND_HOST }}"],
            capture_output=True,
            check=True,
        )
    except Exception:
        log(COMPONENT, "[WARN] Initial DNS check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(
                ["timeout", "5", "getent", "hosts", "{{ .Env.DRAND_HOST }}"],
                capture_output=True,
                check=True,
            )
        except Exception:
            raise SealError("DNS resolution failed (cannot resolve {{ .Env.DRAND_HOST }}).")

    try:
        subprocess.run(
            ["timeout", "5", "bash", "-c", "echo > /dev/tcp/{{ .Env.DRAND_HOST }}/443"],
            capture_output=True,
            check=True,
        )
    except Exception:
        log(COMPONENT, "[WARN] Initial TCP check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(
                ["timeout", "5", "bash", "-c", "echo > /dev/tcp/{{ .Env.DRAND_HOST }}/443"],
                capture_output=True,
                check=True,
            )
        except Exception:
            raise SealError("No internet connectivity (cannot reach {{ .Env.DRAND_HOST }}:443).")

    tle_candidates = ["{{ .Env.TLE_PRIMARY_PATH }}", "{{ .Env.TLE_FALLBACK_PATH }}"]
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
    candidates = ["{{ .Env.TLE_PRIMARY_PATH }}", "{{ .Env.TLE_FALLBACK_PATH }}"]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise SealError("tle not found at {{ .Env.TLE_PRIMARY_PATH }} or {{ .Env.TLE_FALLBACK_PATH }}")


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


# ── System seal gates ────────────────────────────────────────────────────────


def gate_root():
    if os.geteuid() != 0:
        raise SealError("Must be run as root (sudo)")


def gate_unlocked():
    if not os.path.isfile(MODE_FILE):
        return
    with open(MODE_FILE) as f:
        mode = f.read().strip()
    if mode == "locked":
        raise SealError(
            "System is locked. Run 'lockdown unlock' first, then re-run seal."
        )


def gate_openssl():
    if not shutil.which("openssl"):
        raise SealError(
            "openssl not found. Install it with: sudo apt install openssl"
        )


def gate_chpasswd():
    if not shutil.which("chpasswd"):
        raise SealError(
            "chpasswd not found. Install it with: sudo apt install passwd"
        )


def gate_system_cred(cred_path):
    if not os.path.isfile(cred_path):
        raise SealError(
            f"{cred_path} not found.\n"
            f"       Create it with:\n"
            f"         touch {cred_path}\n"
            f"         chmod 600 {cred_path}"
        )


def count_domains(path):
    if not os.path.isfile(path):
        return 0
    count = 0
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
    return count


# ── Credential helpers ──────────────────────────────────────────────────────


def generate_root_password():
    r = subprocess.run(
        ["openssl", "rand", "-base64", "48"],
        capture_output=True, text=True, check=True, timeout=30
    )
    password = r.stdout.strip()
    if not password:
        raise SealError("Failed to generate random password (openssl failed)")
    return password


def set_root_password(password):
    r = subprocess.run(
        ["chpasswd"],
        input=f"root:{password}",
        capture_output=True, text=True, timeout=10
    )
    if r.returncode != 0:
        raise SealError(f"Failed to change root password: {r.stderr.strip()}")


def verify_root_password(password):
    with open("/etc/shadow") as f:
        for line in f:
            if line.startswith("root:"):
                pw_hash = line.strip().split(":")[1]
                break
        else:
            raise SealError(
                "Root account not found in /etc/shadow.\n"
                "       This should never happen — system may be corrupt."
            )
    if not pw_hash or pw_hash in ("!", "*", "!*"):
        raise SealError(
            "Root account is locked or has no password hash.\n"
            "       Run 'passwd root' immediately to set a working password."
        )


def update_cred_file(path, password):
    lines = []
    if os.path.isfile(path):
        with open(path) as f:
            lines = f.readlines()
    lines = [line for line in lines if not line.startswith("root_password=")]
    lines.append(f"root_password={password}\n")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.rename(tmp, path)
    os.chmod(path, 0o600)
    os.chown(path, MIKE_UID, MIKE_GID)


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
BROWSER_CONFIG_DIRS = "{{ .Env.BROWSER_CONFIG_DIRS }}".split()

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
        timeout=int("{{ .Env.TLE_TIMEOUT }}"),
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
                "https://{{ .Env.DRAND_HOST }}/"
                "{{ .Env.DRAND_CHAIN_HASH }}/info"
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


def get_remaining_tle_time(tle_bin, sealed_path):
    import json
    import urllib.request

    if not os.path.isfile(sealed_path):
        raise SealError(f"Sealed file not found: {sealed_path}")

    r = subprocess.run(
        [tle_bin, "-d", "-o", "/dev/null", sealed_path],
        capture_output=True, text=True, timeout=int("{{ .Env.TLE_TIMEOUT }}"),
    )

    if r.returncode == 0:
        raise SealError("TLE has already expired")

    match = re.search(r"round (\d+)", r.stderr)
    if not match:
        raise SealError("Could not determine TLE round from tle output")

    round_num = int(match.group(1))

    global DRAND_CACHE
    if not DRAND_CACHE:
        try:
            url = "https://{{ .Env.DRAND_HOST }}/{{ .Env.DRAND_CHAIN_HASH }}/info"
            req = urllib.request.urlopen(url, timeout=10)
            info = json.loads(req.read())
            DRAND_CACHE["genesis"] = info["genesis_time"]
            DRAND_CACHE["period"] = info["period"]
        except Exception:
            meta_path = os.path.join(SEAL_DIR, "metadata.json")
            if os.path.isfile(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
                seal_ts = meta["seal_timestamp"]
                dur_str = meta["tle_duration"]
                dur_secs = parse_duration(dur_str)
                elapsed = int(time.time()) - seal_ts
                remaining = dur_secs - elapsed
                return max(remaining, 0)
            raise SealError("Cannot determine remaining TLE time "
                            "(drand unreachable, no metadata)")

    unlock_ts = DRAND_CACHE["genesis"] + (round_num - 1) * DRAND_CACHE["period"]
    now = int(time.time())
    remaining = unlock_ts - now
    return max(remaining, 0)


def store_seal_metadata(duration):
    import json
    meta = {"seal_timestamp": int(time.time()), "tle_duration": duration}
    path = os.path.join(SEAL_DIR, "metadata.json")
    tmp = path + ".new"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, path)
    os.chown(path, 0, 0)
    os.chmod(path, 0o644)
    try:
        subprocess.run(["chattr", "+i", path], capture_output=True)
    except Exception:
        pass


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


# ── Duration helpers ─────────────────────────────────────────────────────────


def format_duration(seconds):
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


def parse_duration(s):
    s = s.strip().lower()
    match = re.match(r"^(\d+)([mhd])$", s)
    if not match:
        raise ValueError(f"Invalid duration: {s!r} (use e.g. 30m, 4h, 7d)")
    val, unit = int(match.group(1)), match.group(2)
    return val * {"m": 60, "h": 3600, "d": 86400}[unit]


def prompt_lock_duration(remaining):
    presets = [(1800, "30 minutes"), (3600, "1 hour"), (7200, "2 hours")]
    valid = [(s, label) for s, label in presets if s <= remaining]
    options = {}
    idx = 1
    for s, label in valid:
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
            sys.exit(f"Error: {format_duration(secs)} exceeds remaining "
                     f"TLE time ({remaining_label})")
        return secs
    if not choice:
        print("Aborted.")
        sys.exit(0)
    sys.exit("Error: invalid choice")


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


# ── Seal credentials ─────────────────────────────────────────────────────────


def seal_credentials():
    """Full seal workflow: generate password, encrypt, change root, shred.

    Safe order: encrypt BEFORE changing root password. If tle fails,
    root password is still the old one and the previous sealed file
    is intact as backup.
    """
    global COMPONENT
    cred_path = os.path.join(SEAL_WORK_DIR, "system.credentials")
    sealed_path = os.path.join(SEAL_DIR, "system.sealed")

    COMPONENT = "seal"

    step(COMPONENT, "Checking root access", gate_root)
    step(COMPONENT, "Verifying system state", gate_unlocked)
    step(COMPONENT, "Checking network stability", gate_network)
    tle_bin = step(COMPONENT, "Locating tle binary", gate_tle)

    step(COMPONENT, "Checking openssl", gate_openssl)
    step(COMPONENT, "Checking chpasswd", gate_chpasswd)
    step(COMPONENT, "Verifying system.credentials exists",
             lambda: gate_system_cred(cred_path))

    duration = prompt_duration()
    expiry = compute_expiry(duration)

    confirm("system credentials", cred_path, duration, expiry, [
        "Generate a random root password and change it",
        "Encrypt the credentials with timelock",
        "Permanently shred the plaintext copy",
        "Wipe shell history",
        "Clear clipboard history (cliphist + wl-copy)",
        "Clear browser cache, cookies, and history (Brave, Chrome)",
        "Reboot",
    ])

    print("Generating random root password...")
    log(COMPONENT, "[STEP] Generating random root password...")
    password = generate_root_password()
    log(COMPONENT, "[OK] Random root password generated")

    print("Encrypting credentials with timelock...")
    log(COMPONENT, "[STEP] Encrypting credentials...")
    encrypt(tle_bin, cred_path, sealed_path, duration)
    print("[OK] Encryption complete")

    print("Changing root password...")
    log(COMPONENT, "[STEP] Changing root password...")
    set_root_password(password)
    print(f"New root password: {password}")
    verify_root_password(password)
    update_cred_file(cred_path, password)
    del password
    log(COMPONENT, "[OK] Root password changed, saved to system.credentials")
    print("[OK] Root password changed")

    print("Shredding plaintext credentials...")
    shred_file(cred_path)
    print("[OK] Plaintext shredded")

    print("Clearing clipboard history...")
    clear_clipboard(purge=True)
    print("[OK] Clipboard cleared")

    print("Wiping shell history...")
    wipe_history()
    print("[OK] Shell history wiped")

    print("Clearing browser data...")
    clear_browser_data()
    print("[OK] Browser data cleared")

    print("Storing seal metadata...")
    store_seal_metadata(duration)
    log(COMPONENT, "[OK] Seal metadata stored")


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

    # chattr -i old sealed (don't delete yet — keep as backup)
    if os.path.exists(sealed_path):
        subprocess.run(
            ["sudo", "chattr", "-i", sealed_path], capture_output=True, timeout=10
        )

    tmpdir = tempfile.mkdtemp(prefix="seal_", dir=SEAL_WORK_DIR)
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
        timeout=int("{{ .Env.TLE_TIMEOUT }}"),
            )
        except subprocess.TimeoutExpired:
            raise SealError("tle encryption timed out after {{ .Env.TLE_TIMEOUT }} seconds")

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

        # Delete old sealed file only after new one is verified
        if os.path.exists(sealed_path):
            os.remove(sealed_path)
            log(COMPONENT, "[OK] Old sealed file removed")

        shutil.move(tmp_sealed, sealed_path)
        log(COMPONENT, f"[OK] Sealed credentials written to {sealed_path}")

        if os.geteuid() == 0:
            os.chown(sealed_path, 0, 0)
            os.chown(SEAL_DIR, 0, 0)
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
