#!/usr/bin/python3
"""Unseal credentials with timelock decryption.

Usage:
  unseal -s         Unseal system credentials (decrypt, display, clipboard)
"""

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import seal_lib as lib

lib.COMPONENT = "unseal"


# ── Gates ─────────────────────────────────────────────────────────────────────

def _check_sealed(path):
    if not os.path.isfile(path):
        raise lib.SealError(
            f"Sealed credentials not found at {path}.\n"
            f"       Re-run: seal -s"
        )
    if os.path.getsize(path) == 0:
        raise lib.SealError(f"Sealed credentials file is empty: {path}")


# ── Decryption ────────────────────────────────────────────────────────────────

def _decrypt_atomic(tle_bin, sealed_path, output_path):
    lib._log("unseal", "[STEP] Decrypting...")
    tmpdir = tempfile.mkdtemp(prefix="unseal_", dir=lib.SEAL_DIR)
    lib._register_temp(tmpdir)

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
    lib._log("unseal", "[OK] Decrypted credentials written")


# ── Credential extraction ─────────────────────────────────────────────────────

def _extract_root_password(path):
    with open(path) as f:
        lines = f.readlines()
    for line in reversed(lines):
        if line.startswith("root_password="):
            return line.split("=", 1)[1].strip()
    return None


# ── Clipboard ─────────────────────────────────────────────────────────────────

def _copy_root_password(password):
    try:
        r = subprocess.run(
            ["copyq", "copy"], input=password,
            capture_output=True, timeout=10
        )
        if r.returncode == 0:
            lib._log("unseal", "[OK] Copied to clipboard via copyq")
            return "copyq"
    except Exception as e:
        lib._log("unseal", f"[WARN] copyq copy failed: {e}")

    try:
        r = subprocess.run(
            ["wl-copy"], input=password,
            capture_output=True, timeout=10
        )
        if r.returncode == 0:
            lib._log("unseal", "[OK] Copied to clipboard via wl-copy")
            return "wl-copy"
    except Exception as e:
        lib._log("unseal", f"[WARN] wl-copy failed: {e}")

    return None


# ── Display ───────────────────────────────────────────────────────────────────

def _display_creds(path):
    print("")
    print(f"Contents of {path}:")
    print("----------------------------------------")
    with open(path) as f:
        print(f.read(), end="")
    print("----------------------------------------")
    print("")


# ── Unseal system ─────────────────────────────────────────────────────────────

def unseal_system():
    lib.LOG_FILE = os.path.join(lib.SEAL_DIR, "seal.system.log")

    sealed_path = os.path.join(lib.SEAL_DIR, "system.sealed")
    output_path = os.path.join(lib.SEAL_DIR, "system.credentials")

    lib._ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(lib.LOG_FILE, "a") as f:
        f.write(f"[{ts}] unseal: [START] System unseal started\n")
    lib._log("unseal", "[OK] Log initialized (seal.system.log)")

    lib._step("unseal", "Checking sealed credentials",
              lambda: _check_sealed(sealed_path))
    lib._step("unseal", "Checking network stability", lib._gate_network)
    tle_bin = lib._step("unseal", "Locating tle binary", lib._gate_tle)

    print("Decrypting credentials...")
    _decrypt_atomic(tle_bin, sealed_path, output_path)
    print("[OK] Credentials decrypted")

    _display_creds(output_path)

    password = _extract_root_password(output_path)
    if password:
        method = _copy_root_password(password)
        if method:
            print(f"[OK] Root password copied to clipboard ({method})")
            print("       Paste with $mod+V (Sway) at the 'su -' prompt")
        else:
            print("[--] Could not copy to clipboard — no clipboard manager")
            print("       Select and copy the password above manually")
    else:
        print("[--] No root_password= line found in decrypted credentials")
        print("       Select and copy the password above manually")

    print("")
    print("After logging in as root, change to a simpler password:")
    print("  su -")
    print("  passwd")
    print("  (enter new password twice)")
    print("")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Unseal credentials with timelock decryption"
    )
    parser.add_argument(
        "-s", "--system", action="store_true",
        help="Unseal system credentials (decrypt, display, clipboard)"
    )
    args = parser.parse_args()

    if not args.system:
        parser.print_help()
        sys.exit(1)

    try:
        if args.system:
            unseal_system()
    except lib.SealError as e:
        lib._log("unseal", f"[ERROR] {e}")
        print(f"\n[ERROR] {e}", file=sys.stderr)
        lib._emergency_exit("unseal")
    except Exception as e:
        lib._log("unseal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Unseal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib._emergency_exit("unseal")


if __name__ == "__main__":
    main()
