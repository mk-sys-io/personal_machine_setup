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
import shutil
import subprocess
import sys

import seal_lib as lib

lib.COMPONENT = "seal"

MODE_FILE = "/opt/allowlist/mode"


# ── Seal-specific gates ──────────────────────────────────────────────────────

def gate_root():
    if os.geteuid() != 0:
        raise lib.SealError("Must be run as root (sudo)")


def gate_unlocked():
    if not os.path.isfile(MODE_FILE):
        return
    with open(MODE_FILE) as f:
        mode = f.read().strip()
    if mode == "locked":
        raise lib.SealError(
            "System is locked. Run 'allowlist unlock' first, then re-run seal."
        )


# ── System helpers ────────────────────────────────────────────────────────────

def gate_openssl():
    if not shutil.which("openssl"):
        raise lib.SealError(
            "openssl not found. Install it with: sudo apt install openssl"
        )


def gate_chpasswd():
    if not shutil.which("chpasswd"):
        raise lib.SealError(
            "chpasswd not found. Install it with: sudo apt install passwd"
        )


def gate_system_cred(cred_path):
    if not os.path.isfile(cred_path):
        raise lib.SealError(
            f"{cred_path} not found.\n"
            f"       Create it with:\n"
            f"         touch {cred_path}\n"
            f"         chmod 600 {cred_path}"
        )


# ── System helpers ────────────────────────────────────────────────────────────

def generate_root_password():
    r = subprocess.run(
        ["openssl", "rand", "-base64", "48"],
        capture_output=True, text=True, check=True, timeout=30
    )
    password = r.stdout.strip()
    if not password:
        raise lib.SealError("Failed to generate random password (openssl failed)")
    return password


def set_root_password(password):
    r = subprocess.run(
        ["chpasswd"],
        input=f"root:{password}",
        capture_output=True, text=True, timeout=10
    )
    if r.returncode != 0:
        raise lib.SealError(f"Failed to change root password: {r.stderr.strip()}")


def verify_root_password(password):
    with open("/etc/shadow") as f:
        for line in f:
            if line.startswith("root:"):
                pw_hash = line.strip().split(":")[1]
                break
        else:
            raise lib.SealError(
                "Root account not found in /etc/shadow.\n"
                "       This should never happen — system may be corrupt."
            )
    if not pw_hash or pw_hash in ("!", "*", "!*"):
        raise lib.SealError(
            "Root account is locked or has no password hash.\n"
            "       Run 'passwd root' immediately to set a working password."
        )


def update_cred_file(path, password):
    lines = []
    if os.path.isfile(path):
        with open(path) as f:
            lines = f.readlines()
    lines = [line for line in lines if not line.startswith("root_password=")]
    lines.append(f"root_password={password}\n")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.rename(tmp, path)
    os.chmod(path, 0o600)
    os.chown(path, lib.MIKE_UID, lib.MIKE_GID)


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
    for f in ["/opt/allowlist/allowlist.infra.txt",
              "/opt/allowlist/allowlist.base.txt",
              "/opt/allowlist/allowlist.session.txt"]:
        total += count_domains(f)
    if total == 0:
        raise lib.SealError(
            "All allowlist files are empty. Add domains first:\n"
            "  sudo <editor> /opt/allowlist/allowlist.base.txt"
        )
    subprocess.run(["sudo", "/opt/allowlist/generate-dnsmasq.sh", "locked"],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["sudo", "/opt/allowlist/generate-policies.sh"],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["sudo", "/opt/allowlist/generate-nftables.sh", "locked"],
                   capture_output=True, check=True, timeout=60)
    with open("/opt/allowlist/mode", "w") as f:
        f.write("locked\n")
    lib.log("seal", "[OK] Allowlist locked")


# ── System seal ───────────────────────────────────────────────────────────────

def seal_system():
    cred_path = os.path.join(lib.SEAL_DIR, "system.credentials")
    sealed_path = os.path.join(lib.SEAL_DIR, "system.sealed")
    lib.LOG_FILE = lib.log_path("system")
    lib.init_log("seal", lib.LOG_FILE, "System seal", "w")

    lib.step("seal", "Checking root access", gate_root)
    lib.step("seal", "Verifying system state", gate_unlocked)
    lib.step("seal", "Checking network stability", lib.gate_network)
    tle_bin = lib.step("seal", "Locating tle binary", lib.gate_tle)

    lib.step("seal", "Checking openssl", gate_openssl)
    lib.step("seal", "Checking chpasswd", gate_chpasswd)
    lib.step("seal", "Verifying system.credentials exists",
             lambda: gate_system_cred(cred_path))

    duration = lib.prompt_duration()
    expiry = lib.compute_expiry(duration)

    lib.confirm("system credentials", cred_path, duration, expiry, [
        "Generate a random root password and change it",
        "Append the new password to system.credentials",
        "Encrypt the credentials with timelock",
        "Permanently shred the plaintext copy",
        "Lock the allowlist + firewall",
        "Wipe shell history",
        "Clear clipboard history (copyq + wl-copy)",
        "Clear browser cache, cookies, and history (Brave, Chrome)",
        "Reboot",
    ])

    print("Generating random root password...")
    lib.log("seal", "[STEP] Changing root password...")
    password = generate_root_password()
    set_root_password(password)
    print(f"New root password: {password}")
    verify_root_password(password)
    update_cred_file(cred_path, password)
    del password
    lib.log("seal", "[OK] Root password changed, saved to system.credentials")
    print("[OK] Root password changed")

    print("Encrypting credentials with timelock...")
    lib.encrypt(tle_bin, cred_path, sealed_path, duration)
    print("[OK] Encryption complete")

    print("Shredding plaintext credentials...")
    lib.shred_file(cred_path)
    print("[OK] Plaintext shredded")

    print("Clearing clipboard history...")
    lib.clear_clipboard(purge=True)
    print("[OK] Clipboard cleared")

    print("Locking system...")
    lock_allowlist()
    print("[OK] System locked")

    print("Wiping shell history...")
    lib.wipe_history()
    print("[OK] Shell history wiped")

    print("Clearing browser data...")
    lib.clear_browser_data()
    print("[OK] Browser data cleared")

    print("")
    print("============================================")
    print("  SYSTEM IS NOW LOCKED — Rebooting...")
    print("============================================")
    print("")
    print("To unlock after reboot, wait for the timelock to expire, then run:")
    print("")
    print("  unseal -s")
    print("")
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
    except Exception as e:
        lib.log("seal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Seal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib.emergency_exit("seal")


if __name__ == "__main__":
    main()
