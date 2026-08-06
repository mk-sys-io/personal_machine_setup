#!/usr/bin/python3
"""Cask mobile credentials with timelock encryption.

Usage:
  mcask         Encrypt mobile.credentials, shred plaintext, clear clipboard,
                 wipe shell history.

Pre-flight gates:
  1. Network stability check
  2. tle binary exists
  3. mobile.credentials file exists + non-empty
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/opt/ark/scripts")

import cask_lib as lib
import opslog
from cask_lib import CaskError

# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    cred_path = os.path.join(lib.CASK_WORK_DIR, "mobile.credentials")
    cask_path = os.path.join(lib.CASK_DIR, "mobile.cask")

    log_file = os.path.join(lib.CASK_WORK_DIR, "mcask.log")
    _ = opslog.configure("mcask", file=log_file, mode="w")
    lib.set_component("mcask")
    opslog.session("mcask")

    lib.gate_network()
    tle_bin = lib.gate_tle()

    if os.path.isfile(cask_path) and not lib.check_decrypt_time(tle_bin, cask_path):
        opslog.ok("Mobile cask already present and still locked")
        opslog.end_session("mcask", "OK")
        sys.exit(0)

    lib.gate_cred_file(
        cred_path,
        exists_msg=(
            f"       Create it with:\n"
            f"         echo 'mobile_password=...' > {cred_path}\n"
            f"         chmod 600 {cred_path}"
        ),
    )

    duration = lib.prompt_duration()
    expiry = lib.compute_expiry(duration)

    lib.confirm(
        "mobile credentials",
        cred_path,
        duration,
        expiry,
        [
            "Encrypt the credentials with timelock",
            "Permanently shred the plaintext copy",
            "Clear clipboard history (cliphist + wl-copy)",
            "Wipe shell history",
            "Clear browser cache, cookies, and history (Brave, Chrome)",
            "Reboot the system",
        ],
    )

    lib.cask_credentials_file(tle_bin, cred_path, cask_path, duration)
    lib.reboot()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
    except CaskError:
        lib.emergency_exit("mcask")
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc(file=sys.stderr)
        lib.emergency_exit("mcask")
