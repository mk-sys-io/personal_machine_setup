#!/usr/bin/python3
"""Uncask mobile credentials with timelock decryption.

Usage:
  uncask         Decrypt + display casked mobile credentials.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, "{{ .Env.ARK_DATA_PATH }}/scripts")

import opslog

from cask import CaskError, lib

# ── Decryption ────────────────────────────────────────────────────────────────


def decrypt_atomic(tle_bin: str, cask_path: str, output_path: str) -> None:
    opslog.set_step("Decrypting")
    tmpdir = tempfile.mkdtemp(prefix="uncask_", dir=lib.CASK_WORK_DIR)
    try:
        tmp_out = os.path.join(tmpdir, "credentials")
        try:
            r = subprocess.run(
                [tle_bin, "-d", "-o", tmp_out, cask_path],
                capture_output=True,
                text=True,
                timeout=int("{{ .Env.TLE_TIMEOUT }}"),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CaskError(
                "Decryption timed out after {{ .Env.TLE_TIMEOUT }} seconds"
            ) from None

        if r.returncode != 0:
            stderr_msg = r.stderr.strip() if r.stderr.strip() else "(no stderr)"
            raise CaskError(
                f"Decryption failed (exit {r.returncode}): {stderr_msg}"
            )

        if not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
            raise CaskError(
                "Decryption produced empty output — file may be corrupt"
            )

        os.replace(tmp_out, output_path)
        os.chmod(output_path, 0o600)
        if os.geteuid() == 0:
            os.chown(output_path, lib.MIKE_UID, lib.MIKE_GID)
        opslog.ok("Decrypted credentials written")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Display ───────────────────────────────────────────────────────────────────


def display_creds(path: str) -> None:
    print()
    print(f"Contents of {path}:")
    print("----------------------------------------")
    with open(path) as f:
        print(f.read(), end="")
    print("----------------------------------------")
    print()


# ── Confirmation ──────────────────────────────────────────────────────────────


def _confirm_uncask() -> bool:
    """Non-exiting confirmation box - decline returns False, never sys.exit."""
    print()
    print("=============================================")
    print("  You are about to uncask the mobile credentials.")
    print("=============================================")
    print()
    print("  This will:")
    print("    - Decrypt mobile.cask (timelock must have expired)")
    print("    - Display the plaintext credentials")
    print("    - Leave the decrypted file on disk for manual copy")
    print()
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return False
    return answer in ("y", "yes")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    log_file = os.path.join(lib.CASK_WORK_DIR, "uncask.log")
    _ = opslog.configure("uncask", file=log_file, mode="w")
    lib.set_component("uncask")
    opslog.session("uncask")

    cask_path = os.path.join(lib.CASK_DIR, "mobile.cask")
    lib.gate_cred_file(cask_path, exists_msg="       Re-run: mcask")
    lib.gate_network()
    tle_bin = lib.gate_tle()

    if not lib.check_decrypt_time(tle_bin, cask_path):
        opslog.end_session("uncask", "OK")
        sys.exit(0)

    if not _confirm_uncask():
        print("Cancelled.", file=sys.stderr)
        opslog.end_session("uncask", "OK")
        sys.exit(0)

    output_path = os.path.join(lib.CASK_WORK_DIR, "mobile.credentials")
    print("Decrypting credentials...")
    decrypt_atomic(tle_bin, cask_path, output_path)
    print("[OK] Credentials decrypted")
    display_creds(output_path)
    lib.prompt_manual_copy("password")
    print()
    opslog.end_session("uncask", "OK")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
    except CaskError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        lib.emergency_exit("uncask")
    except (OSError, subprocess.SubprocessError):
        lib.emergency_exit("uncask")
