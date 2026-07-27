#!/usr/bin/python3
"""Seal system credentials with timelock encryption.

Usage:
  seal               Seal system credentials (root password, lockdown, reboot)


Pre-flight gates (runs before any interactive prompts):
  1. Root check
  2. System unlocked check
  3. Network stability check
  4. tle binary exists
  5. Credential file exists + non-empty
"""

import os
import subprocess
import sys

import seal_lib as lib

lib.COMPONENT = "seal"


# ── System helpers ───────────────────────────────────────────────────────────

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


def lock_allowlist():
    total = 0
    # Hardcoded — glob("allowlist.*.txt") would pick up
    # deny.txt (blacklist) as a side effect.
    for f in ["@ARK_DATA_PATH@/infra.txt",
              "@ARK_DATA_PATH@/base.txt",
              "@ARK_DATA_PATH@/session.txt"]:
        total += count_domains(f)
    if total == 0:
        raise lib.SealError(
            "All allowlist files are empty. Add domains first:\n"
            "  sudo <editor> @ARK_DATA_PATH@/base.txt"
        )
    subprocess.run(["@ARK_DATA_PATH@/scripts/generate-dnsmasq.sh", "locked"],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["@ARK_DATA_PATH@/scripts/generate-policies.sh"],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["@ARK_DATA_PATH@/scripts/generate-nftables.sh", "locked"],
                   capture_output=True, check=True, timeout=60)
    with open("@ARK_DATA_PATH@/mode", "w") as f:
        f.write("locked\n")
    lib.log("seal", "[OK] Allowlist locked")


# ── System seal ───────────────────────────────────────────────────────────────

def seal_system():
    lib.LOG_FILE = lib.log_path("system")
    lib.init_log("seal", lib.LOG_FILE, "System seal", "w")

    lib.seal_credentials()
    lock_allowlist()
    print("[OK] System locked")

    print("\n")
    print("============================================")
    print("  SYSTEM IS NOW LOCKED — Rebooting...")
    print("============================================")
    print("\n")
    print("To unlock after reboot, wait for the timelock to expire, then run:")
    print("\n")
    print("  unseal -s")
    print("\n")
    lib.reboot()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    try:
        seal_system()

    except KeyboardInterrupt:
        lib.log("seal", "[END] cancelled")
        print("\nCancelled.")
        sys.exit(0)
    except lib.SealError as e:
        lib.log("seal", f"[ERROR] {e}")
        print(f"\n[ERROR] {e}", file=sys.stderr)
        lib.emergency_exit("seal")
    except (OSError, subprocess.SubprocessError) as e:
        lib.log("seal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Seal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib.emergency_exit("seal")


if __name__ == "__main__":
    main()
