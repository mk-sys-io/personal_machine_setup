from __future__ import annotations

import atexit
import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import opslog

# ── Constants ─────────────────────────────────────────────────────────────────

ARK_DATA_DIR = "{{ .Env.ARK_DATA_PATH }}"
TLE_TIMEOUT = 300
STATE_FILE = f"{ARK_DATA_DIR}/state/enable.json"

# Gomplate markers — rendered from config.env by subst_templates at deploy
# (config.env is gitignored; see config.env.template checklist). DISABLE_ROOT_
# PASSWORD is consumed by `ark disable` (Phase 3), marked here now.
TIMESHIFT_SNAPSHOT_PREFIX = "{{ .Env.TIMESHIFT_SNAPSHOT_PREFIX }}"
BATTERY_THRESHOLD = int("{{ .Env.BATTERY_THRESHOLD }}")
DISABLE_ROOT_PASSWORD = "{{ .Env.DISABLE_ROOT_PASSWORD }}"


# ── Sudo keepalive ────────────────────────────────────────────────────────────

_stop_keepalive = threading.Event()


def _keepalive():
    while not _stop_keepalive.is_set():
        try:
            subprocess.run(["sudo", "-v"], capture_output=True, timeout=10, check=False)
        except (subprocess.SubprocessError, OSError):
            pass
        _stop_keepalive.wait(60)


_keepalive_thread = threading.Thread(target=_keepalive, daemon=True)
_keepalive_thread.start()
atexit.register(_stop_keepalive.set)


# ── Sudo gate helpers ─────────────────────────────────────────────────────────

def deluser_sudo() -> bool:
    user = os.getenv("SUDO_USER") or os.getenv("USER")
    if user is None:
        sys.exit("Error: cannot determine current user")
    result = subprocess.run(["groups", user], capture_output=True, text=True, check=False)
    if "sudo" not in result.stdout:
        return True
    subprocess.run(["deluser", user, "sudo"], check=False)
    result = subprocess.run(["groups", user], capture_output=True, text=True, check=False)
    return "sudo" not in result.stdout


def adduser_sudo() -> bool:
    user = os.getenv("SUDO_USER") or os.getenv("USER")
    if user is None:
        sys.exit("Error: cannot determine current user")
    result = subprocess.run(["groups", user], capture_output=True, text=True, check=False)
    if "sudo" in result.stdout:
        return True
    subprocess.run(["adduser", user, "sudo"], check=False)
    result = subprocess.run(["groups", user], capture_output=True, text=True, check=False)
    return "sudo" in result.stdout


# ── Shared helpers ────────────────────────────────────────────────────────────

def _delete_state() -> None:
    try:
        Path(STATE_FILE).unlink()
        # fsync the parent dir so the gate-close is durable even if the
        # reboot path never flushes (mirror of _write_state's fsync). Best
        # effort — the final sync in reboot_with_countdown is the primary
        # guarantee.
        try:
            fd = os.open(os.path.dirname(STATE_FILE), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            opslog.warn(f"could not fsync {os.path.dirname(STATE_FILE)}: {e}")
    except FileNotFoundError:
        pass


def _get_user() -> str:
    return os.getenv("SUDO_USER") or os.getenv("USER") or "user"


def _format_remaining(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days} days, {hours} hours, {minutes} minutes"
    if hours > 0:
        return f"{hours} hours, {minutes} minutes"
    return f"{minutes} minutes"


# ── Reboot ────────────────────────────────────────────────────────────────────


def reboot_with_countdown(delay: int = 10, *, force: bool = False) -> None:
    """Unified reboot with countdown, brightness persistence, and sync.

    Why two reboot mechanisms in one function:
      - force=False (default): ``systemctl reboot`` — goes through systemd's
        normal shutdown, which runs ``systemd-backlight save`` (ExecStop) so
        the current brightness persists across reboots.
      - force=True: raw ``reboot(2)`` syscall via ctypes — bypasses systemd
        entirely.  Required when the caller has dropped sudo privileges
        (``ark enable`` removes the user from the sudo group before rebooting,
        making ``systemctl reboot`` fail on polkit authorization).

    The countdown is never cancellable.  Every caller has already completed
    its mutations (state writes, credential ops, network lockdown) before
    reaching this point — cancelling the reboot would leave the system in a
    half-applied state that is worse than rebooting.
    """
    print()
    print("============================================")
    print(f"  Rebooting in {delay} seconds...")
    print("============================================")
    for remaining in range(delay, 0, -1):
        print(f"  {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print()

    # Persist brightness so systemd-backlight restores the correct level on
    # next boot.  Belt-and-suspenders: systemctl reboot also triggers
    # systemd-backlight save via ExecStop, but the raw syscall path (force)
    # bypasses it entirely — saving here covers both paths.
    subprocess.run(["brightnessctl", "save"], check=False, capture_output=True)

    # Flush page cache — raw reboot(2) bypasses systemd's shutdown sync, so
    # any unflushed write is lost on reboot.
    subprocess.run(["sync"], check=False)

    if force:
        # Raw reboot(2) syscall — no sudo required, works when systemd is
        # unresponsive or when the caller has dropped privileges.
        try:
            ctypes.CDLL(None).reboot(0x01234567)  # RB_AUTOBOOT
        except OSError as e:
            opslog.error(f"reboot syscall failed ({e})")
            opslog.error("Please reboot manually")
            sys.exit(1)
    else:
        # Normal systemd shutdown — runs ExecStop hooks (including
        # systemd-backlight save) and waits for services to stop cleanly.
        try:
            subprocess.run(["systemctl", "reboot"], check=True)
        except (subprocess.SubprocessError, OSError) as e:
            opslog.error(f"systemctl reboot failed ({e})")
            opslog.error("Please reboot manually")
            sys.exit(1)


LOG_COMMANDS: tuple[str, ...] = ("enable", "disable", "abort")
