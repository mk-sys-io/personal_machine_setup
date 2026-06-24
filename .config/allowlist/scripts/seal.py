#!/usr/bin/python3
"""Seal credentials with timelock encryption.

Usage:
  seal -m         Seal mobile credentials
  seal -s         Seal system credentials (not implemented)
  seal --all      Seal both (not implemented)

Pre-flight gates (runs before any interactive prompts):
  1. Root check
  2. System unlocked check
  3. Network stability check
  4. tle binary exists
  5. Credential file exists + non-empty
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

import seal_lib as lib

lib.COMPONENT = "seal"

MODE_FILE = "/opt/allowlist/mode"


# ── Seal-specific gates ──────────────────────────────────────────────────────

def _gate_root():
    if os.geteuid() != 0:
        raise lib.SealError("Must be run as root (sudo)")


def _gate_unlocked():
    if not os.path.isfile(MODE_FILE):
        return
    with open(MODE_FILE) as f:
        mode = f.read().strip()
    if mode == "locked":
        raise lib.SealError(
            "System is locked. Run 'allowlist unlock' first, then re-run seal."
        )


def _gate_cred_file(cred_path, label):
    if not os.path.isfile(cred_path):
        raise lib.SealError(
            f"{cred_path} not found.\n"
            f"       Create it with:\n"
            f"         echo '{label}_password=<your-{label}-password>' > {cred_path}\n"
            f"         chmod 600 {cred_path}"
        )
    if os.path.getsize(cred_path) == 0:
        raise lib.SealError(f"{cred_path} is empty")


# ── Mobile seal ──────────────────────────────────────────────────────────────

def seal_mobile(args):
    lib.LOG_FILE = os.path.join(lib.SEAL_DIR, "seal.mobile.log")

    cred_path = os.path.join(lib.SEAL_DIR, "mobile.credentials")
    sealed_path = os.path.join(lib.SEAL_DIR, "mobile.sealed")

    lib._ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(lib.LOG_FILE, "w") as f:
        f.write(f"[{ts}] seal: [START] Mobile seal started\n")
    lib._log("seal", "[OK] Log initialized (seal.mobile.log)")

    lib._step("seal", "Checking root access", _gate_root)
    lib._step("seal", "Verifying system state", _gate_unlocked)
    lib._step("seal", "Checking network stability", lib._gate_network)
    tle_bin = lib._step("seal", "Locating tle binary", lib._gate_tle)
    lib._step("seal", "Checking mobile.credentials", lambda: _gate_cred_file(cred_path, "mobile"))

    duration = lib._prompt_duration()
    expiry = lib._compute_expiry(duration)

    lib._confirm("mobile credentials", cred_path, duration, expiry, [
        "Encrypt the credentials with timelock",
        "Permanently shred the plaintext copy",
        "Clear clipboard history (copyq + wl-copy)",
        "Wipe shell history",
        "Reboot",
    ])

    print("Encrypting credentials with timelock...")
    lib._encrypt(tle_bin, cred_path, sealed_path, duration)
    print("[OK] Encryption complete")

    print("Shredding plaintext credentials...")
    try:
        subprocess.run(["shred", "-u", cred_path], capture_output=True, check=True, timeout=30)
    except Exception as e:
        lib._log("seal", f"[WARN] shred failed: {e}, attempting rm -f")
        subprocess.run(["rm", "-f", cred_path], capture_output=True)
    if os.path.exists(cred_path):
        raise lib.SealError(f"Failed to shred {cred_path} — file still exists")
    print("[OK] Plaintext shredded")

    print("Clearing clipboard history...")
    lib._clear_clipboard()
    print("[OK] Clipboard cleared")
    print("Wiping shell history...")
    lib._wipe_history()
    print("[OK] Shell history wiped")
    lib._reboot()


# ── Main ─────────────────────────────────────────────────────────────────────

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
    except lib.SealError as e:
        lib._log("seal", f"[ERROR] {e}")
        print(f"\n[ERROR] Seal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib._emergency_exit("seal")
    except Exception as e:
        lib._log("seal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Seal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib._emergency_exit("seal")


if __name__ == "__main__":
    main()
