#!/usr/bin/python3
"""Cask mobile credentials with timelock encryption.

Usage:
  mcask         Encrypt mobile.credentials, shred plaintext, clear clipboard,
                 wipe shell history.

  cask_mobile(tle_bin, reboot_after=False) - reboot-free core shared with
    "ark enable"'s inline mobile offer (no reboot; the caller owns the tail).

Pre-flight gates:
  1. Network stability check
  2. tle binary exists
  3. mobile.credentials file exists + non-empty
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "{{ .Env.ARK_DATA_PATH }}/scripts")

import opslog

from ark import reboot_with_countdown
from cask import CaskError, lib

# -- Reboot-free core (shared with ark enable inline offer) -----------------


def _confirm_cask(
    cred_path: str, duration: str, expiry: str, items: list[str]
) -> bool:
    """Non-exiting confirmation box - decline returns False, never sys.exit."""
    print()
    print("=============================================")
    print("  You are about to cask the mobile credentials.")
    print("=============================================")
    print()
    print(f"  Timelock:     {duration}")
    print(f"  Expires:      {expiry}")
    print(f"  Credentials:  {cred_path}")
    print()
    print("  This will:")
    for item in items:
        print(f"    - {item}")
    print()
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return False
    return answer in ("y", "yes")


def cask_mobile(tle_bin: str, *, reboot_after: bool = False) -> bool:
    """Cask mobile.credentials without rebooting. True iff casked.

    The caller owns the gates (tle_bin resolved, cred file present, cask
    absent) and the tail (reboot in standalone; continue in "ark enable").
    A decline returns False - never exits the process (inline mobile offers
    are not a hard stop).
    """
    cred_path = os.path.join(lib.CASK_WORK_DIR, "mobile.credentials")
    cask_path = os.path.join(lib.CASK_DIR, "mobile.cask")

    duration = lib.prompt_duration()
    expiry = lib.compute_expiry(duration)

    items = [
        "Encrypt the credentials with timelock",
        "Permanently shred the plaintext copy",
        "Clear clipboard history (cliphist + wl-copy)",
        "Wipe shell history",
        "Clear browser cache, cookies, and history (Chrome, LibreWolf)",
    ]
    if reboot_after:
        items.append("Reboot the system")

    if not _confirm_cask(cred_path, duration, expiry, items):
        return False

    lib.cask_credentials_file(tle_bin, cred_path, cask_path, duration)
    return True


# -- Main --------------------------------------------------------------------


def main() -> None:
    log_file = os.path.join(lib.CASK_WORK_DIR, "mcask.log")
    _ = opslog.configure("mcask", file=log_file, mode="w")
    lib.set_component("mcask")
    opslog.session("mcask")

    lib.gate_network()
    tle_bin = lib.gate_tle()

    cred_path = os.path.join(lib.CASK_WORK_DIR, "mobile.credentials")
    cask_path = os.path.join(lib.CASK_DIR, "mobile.cask")

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

    if cask_mobile(tle_bin, reboot_after=True):
        opslog.end_session("mcask", "OK")
        reboot_with_countdown()
    opslog.end_session("mcask", "OK")


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
