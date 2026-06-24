#!/usr/bin/python3
"""Seal credentials with timelock encryption.

Usage:
  seal -m         Seal mobile credentials
  seal -s         Seal system credentials (root password, lockdown, reboot)
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
import shutil
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


def _gate_empty_cred_file(cred_path):
    if not os.path.isfile(cred_path):
        raise lib.SealError(
            f"{cred_path} not found.\n"
            f"       Create it with:\n"
            f"         touch {cred_path}\n"
            f"         chmod 600 {cred_path}"
        )
    if os.path.getsize(cred_path) != 0:
        raise lib.SealError(
            f"{cred_path} must be empty.\n"
            f"       Clear it with:\n"
            f"         : > {cred_path}"
        )



# ── System helpers ────────────────────────────────────────────────────────────

def _gate_openssl():
    if not shutil.which("openssl"):
        raise lib.SealError(
            "openssl not found. Install it with: sudo apt install openssl"
        )


def _gate_chpasswd():
    if not shutil.which("chpasswd"):
        raise lib.SealError(
            "chpasswd not found. Install it with: sudo apt install passwd"
        )


# ── System helpers ────────────────────────────────────────────────────────────

def _generate_root_password():
    r = subprocess.run(
        ["openssl", "rand", "-base64", "48"],
        capture_output=True, text=True, check=True, timeout=30
    )
    password = r.stdout.strip()
    if not password:
        raise lib.SealError("Failed to generate random password (openssl failed)")
    return password


def _set_root_password(password):
    r = subprocess.run(
        ["chpasswd"],
        input=f"root:{password}",
        capture_output=True, text=True, timeout=10
    )
    if r.returncode != 0:
        raise lib.SealError(f"Failed to change root password: {r.stderr.strip()}")


def _verify_root_password(password):
    import crypt as crypt_mod
    import spwd
    entry = spwd.getspnam("root")
    pw_hash = entry.sp_pwd
    if not pw_hash or pw_hash in ("!", "*", "!*"):
        raise lib.SealError(
            "Root account is locked or has no password hash.\n"
            "       Run 'passwd root' immediately to set a working password."
        )
    if crypt_mod.crypt(password, pw_hash) != pw_hash:
        raise lib.SealError(
            "Root password verification FAILED — crypt mismatch.\n"
            "       The password was changed but does not authenticate.\n"
            "       DO NOT REBOOT. Run 'passwd root' immediately to fix.\n"
            f"       Password (for recovery): {password}"
        )


def _update_cred_file(path, password):
    lines = []
    if os.path.isfile(path):
        with open(path) as f:
            lines = f.readlines()
    lines = [l for l in lines if not l.startswith("root_password=")]
    lines.append(f"root_password={password}\n")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.rename(tmp, path)
    os.chmod(path, 0o600)


def _shred_file(path):
    try:
        subprocess.run(["shred", "-u", path], capture_output=True, check=True, timeout=30)
    except Exception as e:
        lib._log("seal", f"[WARN] shred failed: {e}, attempting rm -f")
        subprocess.run(["rm", "-f", path], capture_output=True)
    if os.path.exists(path):
        raise lib.SealError(f"Failed to shred {path} — file still exists")


def _count_domains(path):
    if not os.path.isfile(path):
        return 0
    count = 0
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
    return count


def _lock_allowlist():
    total = 0
    # Hardcoded — glob("allowlist.*.txt") would pick up
    # deny.txt (blacklist) as a side effect.
    for f in ["/opt/allowlist/allowlist.infra.txt",
              "/opt/allowlist/allowlist.base.txt",
              "/opt/allowlist/allowlist.session.txt"]:
        total += _count_domains(f)
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
    lib._log("seal", "[OK] Allowlist locked")


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


# ── System seal ───────────────────────────────────────────────────────────────

def seal_system(args):
    lib.LOG_FILE = os.path.join(lib.SEAL_DIR, "seal.system.log")

    cred_path = os.path.join(lib.SEAL_DIR, "system.credentials")
    sealed_path = os.path.join(lib.SEAL_DIR, "system.sealed")

    lib._ensure_seal_dir()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(lib.LOG_FILE, "w") as f:
        f.write(f"[{ts}] seal: [START] System seal started\n")
    lib._log("seal", "[OK] Log initialized (seal.system.log)")

    lib._step("seal", "Checking root access", _gate_root)
    lib._step("seal", "Verifying system state", _gate_unlocked)
    lib._step("seal", "Checking network stability", lib._gate_network)
    lib._step("seal", "Checking openssl", _gate_openssl)
    lib._step("seal", "Checking chpasswd", _gate_chpasswd)
    tle_bin = lib._step("seal", "Locating tle binary", lib._gate_tle)
    lib._step("seal", "Verifying system.credentials placeholder",
              lambda: _gate_empty_cred_file(cred_path))

    duration = lib._prompt_duration()
    expiry = lib._compute_expiry(duration)

    lib._confirm("system credentials", cred_path, duration, expiry, [
        "Generate a random root password and change it",
        "Append the new password to system.credentials",
        "Encrypt the credentials with timelock",
        "Permanently shred the plaintext copy",
        "Lock the allowlist + firewall",
        "Wipe shell history",
        "Clear clipboard history (copyq + wl-copy)",
        "Reboot",
    ])

    print("Generating random root password...")
    lib._log("seal", "[STEP] Changing root password...")
    password = _generate_root_password()
    _set_root_password(password)
    print(f"New root password: {password}")
    print("  (write this down if testing with timeshift rollback)")
    _verify_root_password(password)
    _update_cred_file(cred_path, password)
    del password
    lib._log("seal", "[OK] Root password changed, saved to system.credentials")
    print("[OK] Root password changed")

    print("Encrypting credentials with timelock...")
    lib._encrypt(tle_bin, cred_path, sealed_path, duration)
    print("[OK] Encryption complete")

    print("Shredding plaintext credentials...")
    _shred_file(cred_path)
    print("[OK] Plaintext shredded")

    print("Clearing clipboard history...")
    lib._clear_clipboard()
    print("[OK] Clipboard cleared")

    print("Locking system...")
    _lock_allowlist()
    print("[OK] System locked")

    print("Wiping shell history...")
    lib._wipe_history()
    print("[OK] Shell history wiped")

    print("")
    print("============================================")
    print("  SYSTEM IS NOW LOCKED — Rebooting...")
    print("============================================")
    print("")
    print("To unlock after reboot, wait for the timelock to expire, then run:")
    print("")
    print("  unseal")
    print("")
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
            seal_system(args)
        elif args.all:
            print("Error: --all not implemented yet", file=sys.stderr)
            sys.exit(1)
    except lib.SealError as e:
        lib._log("seal", f"[ERROR] {e}")
        print(f"\n[ERROR] {e}", file=sys.stderr)
        lib._emergency_exit("seal")
    except Exception as e:
        lib._log("seal", f"[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"\n[ERROR] Seal failed — see {lib.LOG_FILE} for details", file=sys.stderr)
        lib._emergency_exit("seal")


if __name__ == "__main__":
    main()
