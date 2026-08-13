#!/usr/bin/python3
"""Uncask credentials with timelock decryption.

Usage:
  uncask -s         Uncask system credentials (decrypt, display)
  uncask -m         Uncask mobile credentials (decrypt, display)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, "{{ .Env.ARK_DATA_PATH }}/scripts")

import cask_lib as lib
import opslog
from cask_lib import CaskError

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


# ── Shared init ───────────────────────────────────────────────────────────────


def init_uncask(label: str, cask_path: str, exists_msg: str) -> tuple[str, str]:
    output_path = os.path.join(lib.CASK_WORK_DIR, f"{label}.credentials")
    lib.gate_cred_file(cask_path, exists_msg=exists_msg)
    lib.gate_network()
    tle_bin = lib.gate_tle()
    return output_path, tle_bin


def decrypt_and_show(
    tle_bin: str, cask_path: str, output_path: str, label: str
) -> None:
    if not lib.check_decrypt_time(tle_bin, cask_path):
        opslog.end_session("uncask", "OK")
        sys.exit(0)
    print("Decrypting credentials...")
    decrypt_atomic(tle_bin, cask_path, output_path)
    print("[OK] Credentials decrypted")
    display_creds(output_path)
    lib.prompt_manual_copy(label)
    print()


# ── Uncask system ─────────────────────────────────────────────────────────────


def uncask_system() -> None:
    cask_path = os.path.join(lib.CASK_DIR, "system.cask")
    output_path, tle_bin = init_uncask(
        "system", cask_path, "       Re-run: ark enable"
    )
    decrypt_and_show(tle_bin, cask_path, output_path, "root password")
    print("After logging in as root, change to a simpler password:")
    print("  su -")
    print("  passwd")
    print("  (enter new password twice)")
    print()


# ── Uncask mobile ─────────────────────────────────────────────────────────────


def uncask_mobile() -> None:
    cask_path = os.path.join(lib.CASK_DIR, "mobile.cask")
    output_path, tle_bin = init_uncask(
        "mobile", cask_path, "       Re-run: mcask"
    )
    decrypt_and_show(tle_bin, cask_path, output_path, "password")


# ── Main ──────────────────────────────────────────────────────────────────────


class _Args(argparse.Namespace):
    system: bool = False
    mobile: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Uncask credentials with timelock decryption"
    )
    _ = parser.add_argument(
        "-s", "--system", action="store_true",
        help="Uncask system credentials (decrypt, display)",
    )
    _ = parser.add_argument(
        "-m", "--mobile", action="store_true",
        help="Uncask mobile credentials (decrypt, display)",
    )
    args = parser.parse_args(namespace=_Args())

    if not args.mobile and not args.system:
        parser.print_help()
        sys.exit(1)

    log_file = os.path.join(lib.CASK_WORK_DIR, "uncask.log")
    _ = opslog.configure("uncask", file=log_file, mode="w")
    lib.set_component("uncask")
    opslog.session("uncask")

    try:
        if args.mobile:
            uncask_mobile()
        elif args.system:
            uncask_system()
        opslog.end_session("uncask", "OK")
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
    except CaskError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        lib.emergency_exit("uncask")
    except (OSError, subprocess.SubprocessError):
        lib.emergency_exit("uncask")


if __name__ == "__main__":
    main()
