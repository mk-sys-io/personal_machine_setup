#!/usr/bin/python3
"""System cask operations — root password lifecycle and prereq checks.

Non-interactive infrastructure for ``ark enable``.  Calls the shared core
recipe (``cask_lib.cask_credentials_file``) with a root-password
``after_encrypt`` hook.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import cask_lib as lib
from cask_lib import CaskError

# ── System gates ────────────────────────────────────────────────────────────


def gate_root() -> None:
    if os.geteuid() != 0:
        raise CaskError("Must be run as root (sudo)")


def gate_unlocked() -> None:
    if not os.path.isfile(lib.MODE_FILE):
        return
    with open(lib.MODE_FILE) as f:
        mode = f.read().strip()
    if mode == "locked":
        raise CaskError(
            "System is locked. Run 'lockdown unlock' first, then re-run cask."
        )


def gate_openssl() -> None:
    if not shutil.which("openssl"):
        raise CaskError(
            "openssl not found. Install it with: sudo apt install openssl"
        )


def gate_chpasswd() -> None:
    if not shutil.which("chpasswd"):
        raise CaskError(
            "chpasswd not found. Install it with: sudo apt install passwd"
        )


def gate_system_cred(cred_path: str) -> None:
    if not os.path.isfile(cred_path):
        raise CaskError(
            f"{cred_path} not found.\n" +
            "       Create it with:\n" +
            f"         touch {cred_path}\n" +
            f"         chmod 600 {cred_path}"
        )


# ── Root password lifecycle ─────────────────────────────────────────────────


def generate_root_password() -> str:
    r = subprocess.run(
        ["openssl", "rand", "-base64", "48"],
        capture_output=True, text=True, check=True, timeout=30
    )
    password = r.stdout.strip()
    if not password:
        raise CaskError("Failed to generate random password (openssl failed)")
    return password


def set_root_password(password: str) -> None:
    r = subprocess.run(
        ["chpasswd"],
        input=f"root:{password}",
        capture_output=True, text=True, timeout=10, check=False
    )
    if r.returncode != 0:
        raise CaskError(f"Failed to change root password: {r.stderr.strip()}")


def verify_root_password(_password: str) -> None:
    with open("/etc/shadow") as f:
        for line in f:
            if line.startswith("root:"):
                pw_hash = line.strip().split(":")[1]
                break
        else:
            raise CaskError(
                "Root account not found in /etc/shadow.\n" +
                "       This should never happen — system may be corrupt."
            )
    if not pw_hash or pw_hash in ("!", "*", "!*"):
        raise CaskError(
                "Root account is locked or has no password hash.\n" +
                "       Run 'passwd root' immediately to set a working password."
        )


def update_cred_file(path: str, password: str) -> None:
    lines: list[str] = []
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


def _root_password_lifecycle() -> None:
    """Generate, set, and verify a new root password, then persist it."""
    password = generate_root_password()
    set_root_password(password)
    verify_root_password(password)
    cred_path = os.path.join(lib.CASK_WORK_DIR, "system.credentials")
    update_cred_file(cred_path, password)


# ── Pre-flight ──────────────────────────────────────────────────────────────


def _find_tle() -> str | None:
    for path in ["/usr/local/bin/tle", "/home/mike/go/bin/tle"]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def check_cask_prereqs() -> str:
    """Verify cask prerequisites before confirmation.  Returns tle path."""
    tle_bin = _find_tle()
    if not tle_bin:
        sys.exit("Error: tle binary not found\n" +
                 "  Install: go install github.com/drand/tle/cmd/tle@latest")

    try:
        _ = subprocess.run(
            ["timeout", "5", "getent", "hosts", "{{ .Env.DRAND_HOST }}"],
            capture_output=True, check=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        print("  Checking drand DNS (retrying)...", file=sys.stderr)
        time.sleep(3)
        try:
            _ = subprocess.run(
                ["timeout", "5", "getent", "hosts", "{{ .Env.DRAND_HOST }}"],
                capture_output=True, check=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            sys.exit("Error: cannot reach drand network (DNS failed)\n" +
                     "  Check your internet connection")

    try:
        _ = subprocess.run(
            ["timeout", "5", "bash", "-c",
             "echo > /dev/tcp/{{ .Env.DRAND_HOST }}/443"],
            capture_output=True, check=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        sys.exit("Error: cannot reach drand network (TCP failed)\n" +
                 "  Check firewall/proxy settings")

    try:
        r = subprocess.run(
            [tle_bin, "--metadata"], capture_output=True, text=True,
            timeout=30, check=True,
        )
        if "chain_hash" not in r.stdout:
            sys.exit("Error: tle cannot reach drand timelock network")
    except subprocess.CalledProcessError:
        sys.exit("Error: tle --metadata failed — cannot reach drand")

    cred_path = os.path.join(lib.CASK_WORK_DIR, "system.credentials")
    if not os.path.isfile(cred_path):
        sys.exit(f"Error: {cred_path} not found\n" +
                 "  Run 'ark enable' or create the credentials file")

    if not shutil.which("openssl"):
        sys.exit("Error: openssl not found")

    if not shutil.which("chpasswd"):
        sys.exit("Error: chpasswd not found")

    return tle_bin


# ── System cask wrapper ─────────────────────────────────────────────────────


def cask_credentials(tle_bin: str, duration: str) -> None:
    """Cask system credentials with root-password lifecycle.

    Non-interactive thin wrapper over cask_lib.cask_credentials_file.
    No gates or prompts inside — the caller owns preconditions (run
    check_cask_prereqs once at the boundary).
    """
    cred_path = os.path.join(lib.CASK_WORK_DIR, "system.credentials")
    cask_path = os.path.join(lib.CASK_DIR, "system.cask")
    lib.cask_credentials_file(
        tle_bin, cred_path, cask_path, duration,
        after_encrypt=_root_password_lifecycle,
    )


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    try:
        tle_bin = check_cask_prereqs()
        lib.ensure_cask_dirs()
        cask_credentials(tle_bin, "{{ .Env.TLE_DEFAULT_DURATION }}")
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
    except CaskError:
        lib.emergency_exit("cask")


if __name__ == "__main__":
    main()
