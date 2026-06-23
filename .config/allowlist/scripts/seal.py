#!/usr/bin/python3
"""Seal credentials with timelock encryption.

Usage:
  seal.py -m         Seal mobile credentials
  seal.py -s         Seal system credentials (not implemented)
  seal.py --all      Seal both (not implemented)

Pre-flight gates (runs before any interactive prompts):
  1. Root check
  2. System unlocked check
  3. Network stability check
  4. tle binary exists
  5. Credential file exists + non-empty
"""

import argparse
import atexit
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

# ── Globals ────────────────────────────────────────────────────────────────

MIKE = pwd.getpwnam("mike")
MIKE_UID = MIKE.pw_uid
MIKE_GID = MIKE.pw_gid
HOME_DIR = MIKE.pw_dir
SEAL_DIR = os.path.join(HOME_DIR, ".config", "seal")

LOG_FILE = None  # set per operation type in each seal_*() function
MODE_FILE = "/opt/allowlist/mode"

BROWSER_CACHE_DIRS = [
    os.path.join(HOME_DIR, ".cache", "BraveSoftware"),
    os.path.join(HOME_DIR, ".cache", "google-chrome"),
    os.path.join(HOME_DIR, ".cache", "chromium"),
    os.path.join(HOME_DIR, ".cache", "mozilla"),
    os.path.join(HOME_DIR, ".config", "BraveSoftware", "Brave-Browser", "Default", "Cache"),
    os.path.join(HOME_DIR, ".config", "BraveSoftware", "Brave-Browser", "Default", "Code Cache"),
    os.path.join(HOME_DIR, ".config", "google-chrome", "Default", "Cache"),
    os.path.join(HOME_DIR, ".config", "google-chrome", "Default", "Code Cache"),
    os.path.join(HOME_DIR, ".config", "chromium", "Default", "Cache"),
    os.path.join(HOME_DIR, ".config", "chromium", "Default", "Code Cache"),
]

BROWSER_PROCS = ["brave-browser", "google-chrome", "chromium", "firefox"]

SHELL_HISTORY_FILES = [".bash_history", ".zsh_history", ".zhistory"]

_temp_files = []


# ── Logging + Error handling ───────────────────────────────────────────────

class SealError(Exception):
    pass


def _ensure_seal_dir():
    os.makedirs(SEAL_DIR, exist_ok=True)


def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] seal: {msg}"
    if not LOG_FILE:
        return
    _ensure_seal_dir()
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def _step(name, fn, fatal=True):
    _log(f"[STEP] {name}...")
    try:
        result = fn()
        _log(f"[OK] {name}")
        return result
    except SealError:
        raise
    except Exception as e:
        msg = f"{name}: {e}"
        _log(f"[ERROR] {msg}")
        if fatal:
            _emergency_exit()
        else:
            _log(f"[WARN] {name}: continuing despite failure")
            return None


def _emergency_exit():
    _log("[ERROR] Aborting — plaintext credentials NOT shredded")
    _cleanup_temp_files()
    _log("[END] seal FAILED")
    print(f"\n[ERROR] Seal failed — see {LOG_FILE} for details", file=sys.stderr)
    sys.exit(1)


# ── Temp file management ───────────────────────────────────────────────────

def _register_temp(path):
    _temp_files.append(path)


@atexit.register
def _cleanup_temp_files():
    for path in _temp_files:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            subprocess.run(["shred", "-u", path], capture_output=True)
            if os.path.exists(path):
                os.remove(path)


# ── Signal handling ────────────────────────────────────────────────────────

def _handle_signal(signum, frame):
    _log(f"[ERROR] Received signal {signum}, aborting")
    _emergency_exit()


signal.signal(signal.SIGTERM, _handle_signal)
# SIGINT handled by KeyboardInterrupt propagation through input() try/except


# ── Pre-flight gates ───────────────────────────────────────────────────────

def _gate_root():
    if os.geteuid() != 0:
        raise SealError("Must be run as root (sudo)")


def _gate_unlocked():
    if not os.path.isfile(MODE_FILE):
        return
    with open(MODE_FILE) as f:
        mode = f.read().strip()
    if mode == "locked":
        raise SealError(
            "System is locked. Run 'allowlist unlock' first, then re-run seal."
        )


def _gate_network():
    try:
        subprocess.run(["timeout", "5", "getent", "hosts", "google.com"],
                       capture_output=True, check=True)
    except Exception:
        _log("[WARN] Initial DNS check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(["timeout", "5", "getent", "hosts", "google.com"],
                           capture_output=True, check=True)
        except Exception:
            raise SealError("DNS resolution failed (cannot resolve google.com).")

    try:
        subprocess.run(["timeout", "5", "bash", "-c",
                        "echo > /dev/tcp/1.1.1.1/53"],
                       capture_output=True, check=True)
    except Exception:
        _log("[WARN] Initial TCP check failed, retrying in 3s...")
        time.sleep(3)
        try:
            subprocess.run(["timeout", "5", "bash", "-c",
                            "echo > /dev/tcp/1.1.1.1/53"],
                           capture_output=True, check=True)
        except Exception:
            raise SealError("No internet connectivity (cannot reach 1.1.1.1:53).")

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
        _log("[WARN] tle --metadata failed, retrying in 3s...")
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


def _gate_cred_file(cred_path, label):
    if not os.path.isfile(cred_path):
        raise SealError(
            f"{cred_path} not found.\n"
            f"       Create it with:\n"
            f"         echo '{label}_password=<your-{label}-password>' > {cred_path}\n"
            f"         chmod 600 {cred_path}"
        )
    if os.path.getsize(cred_path) == 0:
        raise SealError(f"{cred_path} is empty")


# ── Interactive prompts ────────────────────────────────────────────────────

def _compute_expiry(duration):
    human = duration.replace("m", " minutes").replace("h", " hours").replace("d", " days")
    try:
        r = subprocess.run(
            ["date", "-u", "-d", f"+{human}", "+%Y-%m-%d %H:%M:%S UTC"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return r.stdout.strip()
        _log(f"[WARN] date command failed (exit {r.returncode}) for duration '{duration}'")
    except Exception as e:
        _log(f"[WARN] date command raised: {e}")
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
        _log("[END] seal cancelled")
        sys.exit(0)

    durations = {"1": "30m", "2": "1h", "3": "3h", "4": "1d", "5": "3d", "6": "7d"}
    if choice in durations:
        return durations[choice]

    if choice == "7":
        try:
            dur = input("Enter duration (e.g. 30m, 4h, 7d): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            _log("[END] seal cancelled")
            sys.exit(0)
        if re.match(r"^\d+[mhd]$", dur):
            return dur
        print("Error: invalid format. Use e.g. 30m, 4h, 7d", file=sys.stderr)
        _log("[END] seal failed — invalid input")
        sys.exit(1)

    if not choice:
        print("Cancelled.")
        _log("[END] seal cancelled")
        sys.exit(0)
    else:
        print("Invalid choice", file=sys.stderr)
        _log("[END] seal failed — invalid input")
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
        _log("[END] seal cancelled")
        sys.exit(0)
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        _log("[END] seal cancelled")
        sys.exit(0)


# ── Discovery helpers ──────────────────────────────────────────────────────

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
            _log(f"[WARN] _discover_session: {e}")
            continue

    xdg_config_home = xdg_config_home or os.path.join(HOME_DIR, ".config")
    xdg_data_home = xdg_data_home or os.path.join(HOME_DIR, ".local", "share")
    return dbus_addr, wayland_display, xdg_data_home, xdg_config_home


# ── Clipboard ──────────────────────────────────────────────────────────────

def _clear_clipboard():
    _log("[STEP] Clearing clipboard history...")
    dbus_addr, wayland_display, xdg_data_home, xdg_config_home = _discover_session()
    xdg_runtime = f"/run/user/{MIKE_UID}"

    base_env = {"XDG_RUNTIME_DIR": xdg_runtime}

    # Wayland clipboard
    if not wayland_display:
        _log("[WARN] WAYLAND_DISPLAY unknown — skipping wl-copy")
    else:
        env = {**base_env, "WAYLAND_DISPLAY": wayland_display}
        try:
            r = subprocess.run(
                ["sudo", "-u", f"#{MIKE_UID}", "wl-copy", "--clear"],
                env=env, capture_output=True, timeout=10
            )
            if r.returncode == 0:
                _log("[OK] Wayland clipboard cleared")
            else:
                _log(f"[WARN] wl-copy --clear failed: {r.stderr.strip()}")
        except Exception as e:
            _log(f"[WARN] wl-copy --clear failed: {e}")

    # CopyQ D-Bus clear
    if not dbus_addr:
        _log("[WARN] DBUS_SESSION_BUS_ADDRESS unknown — skipping copyq clear")
    else:
        env = {**base_env, "DBUS_SESSION_BUS_ADDRESS": dbus_addr}
        try:
            r = subprocess.run(
                ["sudo", "-u", f"#{MIKE_UID}", "copyq", "clear"],
                env=env, capture_output=True, timeout=10
            )
            if r.returncode == 0:
                _log("[OK] CopyQ history cleared via D-Bus")
            else:
                _log(f"[WARN] copyq clear failed: {r.stderr.strip()}")
        except Exception as e:
            _log(f"[WARN] copyq clear failed: {e}")

    # Kill CopyQ
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
                _log("[WARN] copyq force-killed (SIGKILL)")
    except Exception as e:
        _log(f"[WARN] Failed to terminate copyq: {e}")

    # Delete CopyQ data files (preserve config like copyq.conf)
    config_copyq = os.path.join(xdg_config_home, "copyq")
    if os.path.isdir(config_copyq):
        for pattern in ["copyq_tab_*.dat", "copyq_tabs.ini", "copyq.lock"]:
            for f in glob.glob(os.path.join(config_copyq, pattern)):
                try:
                    os.remove(f)
                except Exception as e:
                    _log(f"[WARN] Failed to remove {f}: {e}")

    data_copyq = os.path.join(xdg_data_home, "copyq")
    if os.path.isdir(data_copyq):
        shutil.rmtree(data_copyq, ignore_errors=True)


# ── Browser cache ──────────────────────────────────────────────────────────

def _clear_browser_cache():
    _log("[STEP] Clearing browser cache...")

    # Kill browser processes
    for browser in BROWSER_PROCS:
        try:
            r = subprocess.run(
                ["pgrep", "-u", str(MIKE_UID), "-x", browser],
                capture_output=True, text=True, timeout=5
            )
            if not r.stdout.strip():
                continue
            _log(f"[STEP] Terminating {browser}...")
            subprocess.run(["pkill", "-u", str(MIKE_UID), browser],
                           capture_output=True, timeout=5)
            time.sleep(0.3)
            r2 = subprocess.run(
                ["pgrep", "-u", str(MIKE_UID), "-x", browser],
                capture_output=True, text=True, timeout=5
            )
            if r2.stdout.strip():
                subprocess.run(["pkill", "-9", "-u", str(MIKE_UID), browser],
                               capture_output=True, timeout=5)
                _log(f"[WARN] {browser} force-killed (SIGKILL)")
        except Exception as e:
            _log(f"[WARN] Failed to terminate {browser}: {e}")

    # Delete cache directories
    deleted = 0
    for cache_dir in BROWSER_CACHE_DIRS:
        if os.path.isdir(cache_dir):
            try:
                shutil.rmtree(cache_dir, ignore_errors=True)
                deleted += 1
            except Exception as e:
                _log(f"[WARN] Failed to delete {cache_dir}: {e}")

    if deleted > 0:
        _log(f"[OK] Browser cache cleared ({deleted} directories)")
    else:
        _log("[WARN] No browser cache directories found")


# ── Encryption ─────────────────────────────────────────────────────────────

def _encrypt(tle_bin, cred_path, sealed_path, duration):
    _log("[STEP] Preparing encryption...")

    # Remove immutable flag from old sealed file if present
    subprocess.run(["chattr", "-i", sealed_path], capture_output=True, timeout=10)
    if os.path.exists(sealed_path):
        os.remove(sealed_path)

    # Create temp working directory to ensure same-filesystem rename
    tmpdir = tempfile.mkdtemp(prefix="seal_", dir=SEAL_DIR)
    _register_temp(tmpdir)

    # Copy credentials to temp
    tmp_cred = os.path.join(tmpdir, "credentials")
    shutil.copy2(cred_path, tmp_cred)

    # Encrypt to temp output
    tmp_sealed = os.path.join(tmpdir, "sealed")
    _log(f"[STEP] Running tle -e -D {duration}...")
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

    # Verify output is non-empty
    if not os.path.isfile(tmp_sealed) or os.path.getsize(tmp_sealed) == 0:
        raise SealError("tle produced empty output — encryption failed silently")

    _log(f"[OK] Encryption output verified ({os.path.getsize(tmp_sealed)} bytes)")

    # Atomically move sealed file into place
    shutil.move(tmp_sealed, sealed_path)
    _log(f"[OK] Sealed credentials written to {sealed_path}")

    # Set ownership and permissions
    os.chown(sealed_path, MIKE_UID, MIKE_GID)
    os.chmod(sealed_path, 0o644)

    # Fix seal dir ownership (for seal.log and temp dirs)
    subprocess.run(["chown", "-R", "mike:mike", SEAL_DIR], capture_output=True, check=True)
    _log("[OK] Seal directory ownership set to mike:mike")

    # Apply immutable flag (non-fatal)
    try:
        subprocess.run(["chattr", "+i", sealed_path], capture_output=True, check=True)
        _log("[OK] Immutable flag set on sealed credentials")
    except Exception as e:
        _log(f"[WARN] chattr +i failed: {e} — file not protected (non-fatal)")


# ── History wipe ───────────────────────────────────────────────────────────

def _wipe_history():
    _log("[STEP] Wiping shell history...")
    wiped = 0
    for name in SHELL_HISTORY_FILES:
        path = os.path.join(HOME_DIR, name)
        if os.path.isfile(path):
            try:
                subprocess.run(["shred", "-u", path], capture_output=True, timeout=10)
                wiped += 1
            except Exception as e:
                _log(f"[WARN] Failed to wipe {name}: {e}")
    _log(f"[OK] Shell history wiped ({wiped} files)")


# ── Reboot ─────────────────────────────────────────────────────────────────

def _reboot():
    _log("[STEP] Rebooting in 10 seconds...")
    print("")
    print("============================================")
    print("  Rebooting in 10 seconds...")
    print("============================================")
    print("")
    time.sleep(10)
    _log("[STEP] Rebooting...")
    try:
        subprocess.run(["reboot", "-f"], timeout=5)
    except Exception as e:
        _log(f"[ERROR] reboot failed: {e}")
        _log("[ERROR] Please reboot manually")
        _log("[END] seal FAILED")
        sys.exit(1)


# ── Mobile seal ────────────────────────────────────────────────────────────

def seal_mobile(args):
    global LOG_FILE
    LOG_FILE = os.path.join(SEAL_DIR, "seal.mobile.log")

    cred_path = os.path.join(SEAL_DIR, "mobile.credentials")
    sealed_path = os.path.join(SEAL_DIR, "mobile.sealed")

    # Initialize seal dir + truncate log before any output
    _ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(LOG_FILE, "w") as f:
        f.write(f"[{ts}] seal: [START] Mobile seal started\n")
    _log("[OK] Log initialized (seal.mobile.log)")

    # Pre-flight gates (in order)
    _step("Checking root access", _gate_root)

    _step("Verifying system state", _gate_unlocked)
    _step("Checking network stability", _gate_network)
    tle_bin = _step("Locating tle binary", _gate_tle)
    _step("Checking mobile.credentials", lambda: _gate_cred_file(cred_path, "mobile"))

    # Interactive prompts
    duration = _prompt_duration()
    expiry = _compute_expiry(duration)

    _confirm("mobile credentials", cred_path, duration, expiry, [
        "Encrypt the credentials with timelock",
        "Permanently shred the plaintext copy",
        "Clear clipboard history (copyq + wl-copy)",
        "Clear browser cache (Brave, Chrome, Chromium, Firefox)",
        "Wipe shell history",
        "Reboot",
    ])

    # Encrypt
    print("Encrypting credentials with timelock...")
    _encrypt(tle_bin, cred_path, sealed_path, duration)
    print("[OK] Encryption complete")

    # Shred plaintext
    print("Shredding plaintext credentials...")
    try:
        subprocess.run(["shred", "-u", cred_path], capture_output=True, check=True, timeout=30)
    except Exception as e:
        _log(f"[WARN] shred failed: {e}, attempting rm -f")
        subprocess.run(["rm", "-f", cred_path], capture_output=True)
    if os.path.exists(cred_path):
        raise SealError(f"Failed to shred {cred_path} — file still exists")
    print("[OK] Plaintext shredded")

    # Post-encryption cleanup
    print("Clearing clipboard history...")
    _clear_clipboard()
    print("[OK] Clipboard cleared")
    print("Clearing browser cache...")
    _step("Clearing browser cache", _clear_browser_cache, fatal=False)
    print("[OK] Browser cache cleared")
    print("Wiping shell history...")
    _wipe_history()
    print("[OK] Shell history wiped")
    _reboot()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Seal credentials with timelock encryption",
        epilog="Pre-flight gates: root → unlocked → network → tle → credential file. "
               "These run before any interactive prompts."
    )
    parser.add_argument(
        "-s", "--system", action="store_true",
        help="Seal system credentials (root password, lockdown, reboot)"
    )
    parser.add_argument(
        "-m", "--mobile", action="store_true",
        help="Seal mobile credentials (encrypt, clipboard, browser cache, reboot)"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Seal both system and mobile credentials"
    )
    args = parser.parse_args()

    if not args.system and not args.mobile and not args.all:
        parser.print_help()
        sys.exit(1)

    try:
        if args.mobile:
            seal_mobile(args)
        elif args.system:
            print("Error: -s (system seal) not implemented yet", file=sys.stderr)
            sys.exit(1)
        elif args.all:
            print("Error: --all not implemented yet", file=sys.stderr)
            sys.exit(1)
    except SealError as e:
        _log(f"[ERROR] {e}")
        print(f"\n[ERROR] Seal failed — see {LOG_FILE} for details", file=sys.stderr)
        _emergency_exit()
    except Exception as e:
        _log(f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Seal failed — see {LOG_FILE} for details", file=sys.stderr)
        _emergency_exit()


if __name__ == "__main__":
    main()
