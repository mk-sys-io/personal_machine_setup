#!/usr/bin/python3
"""Unseal credentials with timelock decryption.

Usage:
  unseal -s         Unseal system credentials (decrypt, display)
  unseal -m         Unseal mobile credentials (decrypt, display)
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import seal_lib as lib

lib.COMPONENT = "unseal"


# ── Decryption ────────────────────────────────────────────────────────────────

def decrypt_atomic(tle_bin, sealed_path, output_path):
    lib.log("unseal", "[STEP] Decrypting...")
    tmpdir = tempfile.mkdtemp(prefix="unseal_", dir=lib.SEAL_DIR)
    try:
        tmp_out = os.path.join(tmpdir, "credentials")
        try:
            r = subprocess.run(
                [tle_bin, "-d", "-o", tmp_out, sealed_path],
                capture_output=True, text=True, timeout=300
            )
        except subprocess.TimeoutExpired:
            raise lib.SealError("Decryption timed out after 300 seconds")

        if r.returncode != 0:
            stderr_msg = r.stderr.strip() if r.stderr.strip() else "(no stderr)"
            raise lib.SealError(
                f"Decryption failed (exit {r.returncode}): {stderr_msg}"
            )

        if not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
            raise lib.SealError("Decryption produced empty output — file may be corrupt")

        os.replace(tmp_out, output_path)
        os.chmod(output_path, 0o600)
        lib.log("unseal", "[OK] Decrypted credentials written")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Display ───────────────────────────────────────────────────────────────────

def display_creds(path):
    print("")
    print(f"Contents of {path}:")
    print("----------------------------------------")
    with open(path) as f:
        print(f.read(), end="")
    print("----------------------------------------")
    print("")


# ── Shared init ──────────────────────────────────────────────────────────────

def init_unseal(label, sealed_path, exists_msg):
    output_path = os.path.join(lib.SEAL_DIR, f"{label}.credentials")
    lib.LOG_FILE = lib.log_path(label)
    lib.init_log("unseal", lib.LOG_FILE, f"{label.capitalize()} unseal", "a")
    lib.step("unseal", "Checking sealed credentials",
              lambda: lib.gate_cred_file(sealed_path, exists_msg=exists_msg))
    lib.step("unseal", "Checking network stability", lib.gate_network)
    tle_bin = lib.step("unseal", "Locating tle binary", lib.gate_tle)
    return output_path, tle_bin


def decrypt_and_show(tle_bin, sealed_path, output_path, label):
    if not lib.check_decrypt_time(tle_bin, sealed_path):
        sys.exit(0)
    print("Decrypting credentials...")
    decrypt_atomic(tle_bin, sealed_path, output_path)
    print("[OK] Credentials decrypted")
    display_creds(output_path)
    lib.prompt_manual_copy(label)
    print("")


# ── Unseal system ─────────────────────────────────────────────────────────────

def unseal_system():
    sealed_path = os.path.join(lib.SEAL_DIR, "system.sealed")
    output_path, tle_bin = init_unseal("system", sealed_path, "       Re-run: seal -s")
    decrypt_and_show(tle_bin, sealed_path, output_path, "root password")
    print("After logging in as root, change to a simpler password:")
    print("  su -")
    print("  passwd")
    print("  (enter new password twice)")
    print("")


# ── Unseal mobile ─────────────────────────────────────────────────────────────

def unseal_mobile():
    sealed_path = os.path.join(lib.SEAL_DIR, "mobile.sealed")
    output_path, tle_bin = init_unseal("mobile", sealed_path, "       Re-run: seal -m")
    decrypt_and_show(tle_bin, sealed_path, output_path, "password")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Unseal credentials with timelock decryption"
    )
    parser.add_argument(
        "-s", "--system", action="store_true",
        help="Unseal system credentials (decrypt, display)"
    )
    parser.add_argument(
        "-m", "--mobile", action="store_true",
        help="Unseal mobile credentials (decrypt, display)"
    )
    args = parser.parse_args()

    if not args.mobile and not args.system:
        parser.print_help()
        sys.exit(1)

    try:
        if args.mobile:
            unseal_mobile()
        elif args.system:
            unseal_system()
    except KeyboardInterrupt:
        lib.log("unseal", "[END] cancelled")
        print("\nCancelled.")
        sys.exit(0)
    except lib.SealError as e:
        lib.log("unseal", f"[ERROR] {e}")
        print(f"\n[ERROR] {e}", file=sys.stderr)
        lib.emergency_exit("unseal")
    except Exception as e:
        lib.log("unseal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Unseal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib.emergency_exit("unseal")


if __name__ == "__main__":
    main()
