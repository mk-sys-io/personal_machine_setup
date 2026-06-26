#!/usr/bin/python3
"""Seal mobile credentials with timelock encryption.

Usage:
  sem    Encrypt mobile.credentials, shred plaintext, clear clipboard,
         wipe shell history.

Pre-flight gates:
  1. Network stability check
  2. tle binary exists
  3. mobile.credentials file exists + non-empty
"""

import os
import sys

import seal_lib as lib

lib.COMPONENT = "seal"


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    lib.LOG_FILE = lib.log_path("mobile")

    cred_path = os.path.join(lib.SEAL_DIR, "mobile.credentials")
    sealed_path = os.path.join(lib.SEAL_DIR, "mobile.sealed")

    lib.init_log("seal", lib.LOG_FILE, "Mobile seal", "w")

    lib.step("seal", "Checking network stability", lib.gate_network)
    tle_bin = lib.step("seal", "Locating tle binary", lib.gate_tle)

    if os.path.isfile(sealed_path) and not lib.check_decrypt_time(tle_bin, sealed_path):
        sys.exit(0)

    lib.step("seal", "Checking mobile.credentials",
              lambda: lib.gate_cred_file(cred_path,
                exists_msg=f"       Create it with:\n"
                           f"         echo 'mobile_password=...' > {cred_path}\n"
                           f"         chmod 600 {cred_path}"))

    duration = lib.prompt_duration()
    expiry = lib.compute_expiry(duration)

    lib.confirm("mobile credentials", cred_path, duration, expiry, [
        "Encrypt the credentials with timelock",
        "Permanently shred the plaintext copy",
        "Clear clipboard history (copyq + wl-copy)",
        "Wipe shell history",
        "Reboot the system",
    ])

    print("Encrypting credentials with timelock...")
    lib.encrypt(tle_bin, cred_path, sealed_path, duration)
    print("[OK] Encryption complete")

    print("Shredding plaintext credentials...")
    lib.shred_file(cred_path)
    print("[OK] Plaintext shredded")

    print("Clearing clipboard history...")
    lib.clear_clipboard(purge=True)
    print("[OK] Clipboard cleared")
    print("Wiping shell history...")
    lib.wipe_history()
    print("[OK] Shell history wiped")

    print("Rebooting...")
    lib.reboot()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        lib.log("seal", "[END] cancelled")
        print("\nCancelled.")
        sys.exit(0)
    except lib.SealError as e:
        lib.log("seal", f"[ERROR] {e}")
        print(f"\n[ERROR] {e}", file=sys.stderr)
        lib.emergency_exit("seal")
    except Exception as e:
        lib.log("seal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Seal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib.emergency_exit("seal")
