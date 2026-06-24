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
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import seal_lib as lib

lib.COMPONENT = "unseal"


# ── Unseal-specific gates ────────────────────────────────────────────────────

def _gate_sealed_file(sealed_path):
    if not os.path.isfile(sealed_path):
        raise lib.SealError(
            f"{sealed_path} not found.\n"
            f"       Run 'seal -m' first to create it."
        )
    if os.path.getsize(sealed_path) == 0:
        raise lib.SealError(f"{sealed_path} is empty")


def _gate_no_conflict(cred_path):
    if os.path.isfile(cred_path):
        raise lib.SealError(
            f"{cred_path} already exists.\n"
            f"       Remove it first to re-decrypt:\n"
            f"         rm -f {cred_path}"
        )


# ── Decryption ───────────────────────────────────────────────────────────────

def _unseal_mobile(tle_bin, sealed_path, cred_path):
    lib._ensure_seal_dir()

    tmpdir = tempfile.mkdtemp(prefix="unseal_", dir=lib.SEAL_DIR)
    lib._register_temp(tmpdir)
    tmp_out = os.path.join(tmpdir, "credentials")

    print("Decrypting credentials with timelock...")
    try:
        r = subprocess.run(
            [tle_bin, "-d", "-o", tmp_out, sealed_path],
            capture_output=True, text=True, timeout=300
        )
    except subprocess.TimeoutExpired:
        raise lib.SealError("tle decryption timed out after 300 seconds")

    if r.returncode != 0:
        stderr_msg = r.stderr.strip() if r.stderr.strip() else "(no stderr)"
        raise lib.SealError(f"tle decryption failed (exit {r.returncode}): {stderr_msg}")

    if not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
        raise lib.SealError("tle produced empty output — decryption failed silently")

    lib._log("unseal", f"[OK] Decryption successful ({os.path.getsize(tmp_out)} bytes)")

    os.chmod(tmp_out, 0o600)
    shutil.move(tmp_out, cred_path)
    print("[OK] Decryption complete")
    lib._log("unseal", f"[OK] Credentials written to {cred_path}")


# ── Main ─────────────────────────────────────────────────────────────────────

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

    lib.LOG_FILE = os.path.join(lib.SEAL_DIR, "unseal.mobile.log")

    cred_path = os.path.join(lib.SEAL_DIR, "mobile.credentials")
    sealed_path = os.path.join(lib.SEAL_DIR, "mobile.sealed")

    lib._ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(lib.LOG_FILE, "w") as f:
        f.write(f"[{ts}] unseal: [START] Mobile unseal started\n")
    lib._log("unseal", "[OK] Log initialized (unseal.mobile.log)")

    try:
        tle_bin = lib._step("unseal", "Locating tle binary", lib._gate_tle)
        lib._step("unseal", "Checking mobile.sealed", lambda: _gate_sealed_file(sealed_path))
        lib._step("unseal", "Checking for conflicts", lambda: _gate_no_conflict(cred_path))
        lib._step("unseal", "Checking network stability", lib._gate_network)

        print("")
        print("  WARNING: Do not cancel or close this terminal during decryption.")
        print("  Interrupting may leave partial credentials that require manual cleanup.")
        print("")
        lib._log("unseal", "[STEP] Decryption starting — do not cancel")

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

        lib._log("unseal", "[END] unseal SUCCESS")
    except KeyboardInterrupt:
        lib._log("unseal", "[END] unseal cancelled")
        sys.exit(0)
    except SystemExit:
        pass
    except lib.SealError as e:
        lib._log("unseal", f"[ERROR] {e}")
        lib._emergency_exit("unseal")
    except Exception as e:
        lib._log("unseal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        lib._emergency_exit("unseal")


if __name__ == "__main__":
    main()
