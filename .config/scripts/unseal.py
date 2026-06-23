#!/usr/bin/python3
"""Unseal mobile credentials with timelock decryption.

Usage:
  unseal -m         Unseal mobile credentials

No root check required — runs as user.
Works in both locked and unlocked modes (drand endpoints are allowlisted).

Pre-flight gates (runs before decryption):
  1. tle binary exists
  2. mobile.sealed exists + non-empty
  3. mobile.credentials does not already exist (refuse to overwrite)
  4. Network stability check (drand beacons reachable)
"""

import argparse
import atexit
import os
import pwd
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

LOG_FILE = None

# ── Logging + Error handling ───────────────────────────────────────────────

class SealError(Exception):
    pass


def _ensure_seal_dir():
    os.makedirs(SEAL_DIR, exist_ok=True)


def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] unseal: {msg}"
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
    _log("[ERROR] Aborting")
    _cleanup_temp_files()
    _log("[END] unseal FAILED")
    print(f"\n[ERROR] Unseal failed — see {LOG_FILE} for details", file=sys.stderr)
    sys.exit(1)


# ── Temp file management ───────────────────────────────────────────────────

_temp_files = []


@atexit.register
def _cleanup_temp_files():
    for path in _temp_files:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            subprocess.run(["shred", "-u", path], capture_output=True)
            if os.path.exists(path):
                os.remove(path)


def _register_temp(path):
    _temp_files.append(path)


# ── Signal handling ────────────────────────────────────────────────────────

def _handle_signal(signum, frame):
    _log(f"[ERROR] Received signal {signum}, aborting")
    _emergency_exit()


signal.signal(signal.SIGTERM, _handle_signal)


# ── Pre-flight gates ───────────────────────────────────────────────────────

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


def _gate_sealed_file(sealed_path):
    if not os.path.isfile(sealed_path):
        raise SealError(
            f"{sealed_path} not found.\n"
            f"       Run 'seal -m' first to create it."
        )
    if os.path.getsize(sealed_path) == 0:
        raise SealError(f"{sealed_path} is empty")


def _gate_no_conflict(cred_path):
    if os.path.isfile(cred_path):
        raise SealError(
            f"{cred_path} already exists.\n"
            f"       Remove it first to re-decrypt:\n"
            f"         rm -f {cred_path}"
        )


# ── Decryption ─────────────────────────────────────────────────────────────

def _unseal_mobile(tle_bin, sealed_path, cred_path):
    _ensure_seal_dir()

    tmpdir = tempfile.mkdtemp(prefix="unseal_", dir=SEAL_DIR)
    _register_temp(tmpdir)
    tmp_out = os.path.join(tmpdir, "credentials")

    print("Decrypting credentials with timelock...")
    try:
        r = subprocess.run(
            [tle_bin, "-d", "-o", tmp_out, sealed_path],
            capture_output=True, text=True, timeout=300
        )
    except subprocess.TimeoutExpired:
        raise SealError("tle decryption timed out after 300 seconds")

    if r.returncode != 0:
        stderr_msg = r.stderr.strip() if r.stderr.strip() else "(no stderr)"
        raise SealError(f"tle decryption failed (exit {r.returncode}): {stderr_msg}")

    if not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
        raise SealError("tle produced empty output — decryption failed silently")

    _log(f"[OK] Decryption successful ({os.path.getsize(tmp_out)} bytes)")

    os.chmod(tmp_out, 0o600)
    shutil.move(tmp_out, cred_path)
    print("[OK] Decryption complete")
    _log(f"[OK] Credentials written to {cred_path}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Unseal mobile credentials with timelock decryption."
    )
    parser.add_argument(
        "-m", "--mobile", action="store_true",
        help="Unseal mobile credentials from mobile.sealed"
    )
    args = parser.parse_args()

    if not args.mobile:
        parser.print_help()
        sys.exit(1)

    global LOG_FILE
    LOG_FILE = os.path.join(SEAL_DIR, "unseal.mobile.log")

    cred_path = os.path.join(SEAL_DIR, "mobile.credentials")
    sealed_path = os.path.join(SEAL_DIR, "mobile.sealed")

    _ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(LOG_FILE, "w") as f:
        f.write(f"[{ts}] unseal: [START] Mobile unseal started\n")
    _log("[OK] Log initialized (unseal.mobile.log)")

    try:
        tle_bin = _step("Locating tle binary", _gate_tle)
        _step("Checking mobile.sealed", lambda: _gate_sealed_file(sealed_path))
        _step("Checking for conflicts", lambda: _gate_no_conflict(cred_path))
        _step("Checking network stability", _gate_network)

        print("")
        print("  WARNING: Do not cancel or close this terminal during decryption.")
        print("  Interrupting may leave partial credentials that require manual cleanup.")
        print("")
        _log("[STEP] Decryption starting — do not cancel")

        _unseal_mobile(tle_bin, sealed_path, cred_path)

        with open(cred_path) as f:
            contents = f.read()

        print("")
        print("============================================")
        print("  Mobile credentials decrypted successfully")
        print("============================================")
        print("")
        print(f"  Path: {cred_path}")
        print("")
        print("Contents:")
        print("--------------------------------------------")
        print(contents, end="")
        print("--------------------------------------------")
        print("")

        _log("[END] unseal SUCCESS")
    except KeyboardInterrupt:
        _log("[END] unseal cancelled")
        sys.exit(0)
    except SystemExit:
        pass
    except SealError as e:
        _log(f"[ERROR] {e}")
        _emergency_exit()
    except Exception as e:
        _log(f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        _emergency_exit()


if __name__ == "__main__":
    main()
