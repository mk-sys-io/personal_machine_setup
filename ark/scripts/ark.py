#!/usr/bin/env python3
"""Ark — internet lockdown CLI.

Usage:
  ark enable     Enable focused mode (blocklist + cask)
  ark disable    Disable focused mode
  ark abort --now  Roll back an incomplete enable (snapshot restore)
  ark logs [cmd]  Show per-command logs (enable/disable/abort)

Requires root. Deployed to /usr/local/bin/ark (root:root 755).

State mutation note: the enable/disable subcommands apply changes in a
specific order (cask first, network configs second, sudo removal last).
If any step fails mid-sequence, the system may be in a partial state.
`ark abort --now` rolls back to the pre-enable timeshift snapshot.
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import re
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

if os.geteuid() != 0:
    sys.exit("Error: ark requires root\n  Run: sudo ark <command>")

# cask_lib, mode, netmgr, and opslog live in /opt/ark/scripts/
sys.path.insert(0, "/opt/ark/scripts")
import abort
import cask_lib as lib
import cask_system
import immutable_lib
import mcask
import mode
import netmgr
import opslog
from cask_lib import CaskError

# ── Constants ─────────────────────────────────────────────────────────────────

ARK_DATA_DIR = "/opt/ark"
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


# ── Precondition wrapper ──────────────────────────────────────────────────────

def require(check, *args, msg=None):
    """Run a guard, exiting cleanly on PrereqError/NetworkError (audit H1).

    netmgr.guards raise on failure; local bool-returning helpers (e.g.
    adduser_sudo) return False. Both are handled here.
    """
    try:
        result = check(*args)
    except (netmgr.guards.PrereqError, netmgr.guards.NetworkError) as e:
        sys.exit(msg or f"Error: {e}")
    if result is False:
        sys.exit(msg or f"Error: {check.__name__} check failed")
    print(f"  ✓ {check.__name__}")


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


# ── Enable helpers ────────────────────────────────────────────────────────────


class _EnableError(RuntimeError):
    """Fatal enable failure with a user-facing message."""


def _confirm(prompt: str, *, default: bool = False) -> bool:
    """y/N prompt. KeyboardInterrupt propagates; EOF falls back to default."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return default
    if answer in ("y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    return default


def _battery_capacity() -> int | None:
    """Percent (0-100) of the first battery found; None when none present."""
    power_dir = Path("/sys/class/power_supply")
    if not power_dir.is_dir():
        return None
    for entry in sorted(power_dir.iterdir()):
        if not entry.name.startswith("BAT"):
            continue
        cap = entry / "capacity"
        if cap.is_file():
            try:
                return int(cap.read_text().strip())
            except (OSError, ValueError):
                continue
    return None


def _gate_battery() -> None:
    """BATTERY_THRESHOLD gate: hard stop below threshold, skip if no battery."""
    capacity = _battery_capacity()
    if capacity is None:
        opslog.info("no battery present — battery gate skipped")
        return
    if capacity < BATTERY_THRESHOLD:
        raise netmgr.guards.PrereqError(
            f"battery at {capacity}% — below BATTERY_THRESHOLD "
            f"({BATTERY_THRESHOLD}%)\n"
            "  Plug in power or wait until charged, then re-run: ark enable"
        )
    opslog.ok(f"battery {capacity}% ≥ threshold {BATTERY_THRESHOLD}%")


def _timeshift_list() -> set[str]:
    """Canonical snapshot ids from `timeshift --list` (locale-stable)."""
    result = subprocess.run(
        ["timeshift", "--list"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise _EnableError(
            f"timeshift --list failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return abort.parse_list(result.stdout)


def _snapshot_create() -> str:
    """Create a snapshot with the configured prefix; return the canonical id.

    timeshift is gated ark-exclusive in preflight, so the newest listed
    snapshot is the one just created. Canonical format (YYYY-MM-DD HH:MM:SS)
    sorts lexically and matches abort's verify_snapshot normalization.
    """
    result = subprocess.run(
        ["timeshift", "--create", "--comments", TIMESHIFT_SNAPSHOT_PREFIX],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise _EnableError(
            f"timeshift --create failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    listed = _timeshift_list()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    candidates = [s for s in listed if s <= now]
    if not candidates:
        raise _EnableError(
            "timeshift --create succeeded but no snapshot listed — abort "
            "refused; inspect timeshift manually"
        )
    return max(candidates)


def _timeshift_delete(snapshot_id: str) -> None:
    result = subprocess.run(
        ["timeshift", "--delete", "--snapshot", snapshot_id],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise _EnableError(
            f"timeshift --delete {snapshot_id} failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _write_state(snapshot_id: str) -> None:
    """Write enable.json atomically (tmp → fsync → rename), 0600, no +i.

    The abort restore-hook must be able to delete it — never immutable.
    """
    payload = {
        "snapshot_id": snapshot_id,
        "mode": "unrestricted",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = Path(f"{STATE_FILE}.tmp")
    with tmp.open("w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chown(tmp, 0, 0)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


def _delete_state() -> None:
    try:
        Path(STATE_FILE).unlink()
    except FileNotFoundError:
        pass


def _stdin_line_ready() -> bool:
    """True when a line is waiting on stdin (countdown cancel)."""
    try:
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (ValueError, OSError):
        return False


def _reboot_tail() -> None:
    """Final sequence: countdown → sync → END enable: OK → delete state → reboot.

    The state file is deleted as the last step before the reboot syscall —
    a kill in any window before delete leaves the abort gate armed.
    """
    print()
    print("============================================")
    print("  Rebooting in 10 seconds (Enter to cancel)")
    print("============================================")
    for remaining in range(10, 0, -1):
        print(f"  {remaining}...", end="\r", flush=True)
        if _stdin_line_ready():
            print("\nCancelled.", file=sys.stderr)
            opslog.end_session("enable", "FAILED")
            sys.exit(0)
        time.sleep(1)
    print()

    subprocess.run(["sync"], check=False)
    opslog.end_session("enable", "OK")
    _delete_state()

    # Direct reboot syscall — no sudo, works even when systemd wedges.
    try:
        ctypes.CDLL(None).reboot(0x01234567)  # RB_AUTOBOOT
    except OSError as e:
        opslog.error(f"reboot syscall failed ({e})")
        opslog.error("Please reboot manually — enable completed, state file deleted")
        sys.exit(1)


def _mobile_offer(tle_bin: str) -> None:
    """Confirmation 1 — inline mobile cask offer. Never a hard stop."""
    cask_path = os.path.join(lib.CASK_DIR, "mobile.cask")
    cred_path = os.path.join(lib.CASK_WORK_DIR, "mobile.credentials")
    if os.path.isfile(cask_path):
        opslog.info("mobile.cask present — skipping mobile offer")
        return
    if not os.path.isfile(cred_path):
        opslog.warn("no mobile.credentials — no mobile lock offered")
        return
    print("\n  Mobile credentials found but not casked.")
    if not _confirm("  Cask mobile.credentials now? [y/N] ", default=False):
        opslog.warn("mobile cask declined — proceeding")
        return
    if mcask.cask_mobile(tle_bin):
        opslog.ok("mobile credentials casked")
    else:
        opslog.warn("mobile cask declined — proceeding")


def cmd_enable() -> None:
    """Transactional focused-mode entry (ark-enable-flow.md)."""
    try:
        _run_enable()
    except _EnableError as e:
        opslog.error(str(e))
        opslog.end_session("enable", "FAILED")
        sys.exit(f"Error: {e}")
    except (netmgr.guards.PrereqError, netmgr.guards.NetworkError,
            abort.AbortError) as e:
        opslog.error(str(e))
        opslog.end_session("enable", "FAILED")
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        opslog.end_session("enable", "FAILED")
        print("\nCancelled.", file=sys.stderr)
        sys.exit(0)


def _run_enable() -> None:
    user = _get_user()

    # ── Phase 1 — Preflight (no state mutation) ───────────────────────────────
    opslog.set_step("Preflight")
    netmgr.guards.check_lockdown_dir()
    opslog.ok("lockdown dir present")
    netmgr.guards.check_scripts()
    opslog.ok("scripts executable")
    netmgr.guards.check_blocklist_dnsmasq()
    opslog.ok("blocklist.dnsmasq.conf present")
    netmgr.guards.audit_package_managers()
    opslog.ok("no conflicting package managers")
    _gate_battery()
    tle_bin = netmgr.guards.check_prereqs()
    opslog.ok("network + credential prerequisites pass")
    abort.timeshift_exclusive()
    opslog.ok("timeshift ark-exclusive")

    mode.ensure()
    current = mode.read()
    if current != "unrestricted":
        raise _EnableError(f"cannot enable from {current} mode")

    # ── Phase 2 — Confirmations 1-2 + snapshot + state file ───────────────────
    opslog.set_step("Mobile cask")
    _mobile_offer(tle_bin)

    opslog.set_step("Impact confirmation")
    print("\n  Enable Focus Mode\n")
    print("  State: unrestricted -> focused\n")
    print("  This will:")
    print(f"    - Remove sudo access for {user}")
    print("    - Deploy browser policies (Brave, Chromium, Chrome, Firefox)")
    print("    - Deploy bookmarks")
    print("    - Configure dnsmasq allowlist (focused)")
    print("    - Apply nftables firewall rules (focused)")
    print("    - Cask system credentials with TLE")
    print("    - Reboot\n")
    if not _confirm("  Proceed? [y/N] ", default=False):
        opslog.end_session("enable", "FAILED")
        sys.exit(0)

    opslog.set_step("Snapshot")
    snapshot_id = _snapshot_create()
    opslog.ok(f"snapshot {snapshot_id} created ({TIMESHIFT_SNAPSHOT_PREFIX})")
    _write_state(snapshot_id)
    opslog.ok("enable.json written — abort gate armed")

    # ── Phase 3 — Final confirmation + mutation (point of no return) ──────────
    opslog.set_step("Final confirmation")
    duration = lib.prompt_duration()
    while True:
        print("\n============================================")
        print("  Root password will be randomized and encrypted with TLE.")
        print(f"  Encryption time: {duration}")
        print("  1) Proceed")
        print("  2) Change encryption time")
        print("  3) Abort")
        try:
            choice = input("  Choice: ").strip()
        except EOFError:
            choice = "3"
        if choice == "1":
            break
        if choice == "2":
            duration = lib.prompt_duration()
            continue
        if choice == "3":
            opslog.info(f"aborting enable — deleting snapshot {snapshot_id}")
            _timeshift_delete(snapshot_id)
            _delete_state()
            raise _EnableError(
                "enable aborted — snapshot deleted, state file removed "
                "(nothing to abort)"
            )
        print("  Invalid choice")

    opslog.set_step("Cask system credentials")
    try:
        cask_system.cask_credentials(tle_bin, duration)
    except CaskError as e:
        raise _EnableError(
            f"cask failed ({e})\n  Run: ark abort --now to roll back "
            "(gate still armed)"
        ) from None

    opslog.set_step("Network lockdown")
    try:
        netmgr.policies.deploy()
        netmgr.dns.configure("focused")
        netmgr.firewall.apply("focused")
        # Rebuild shims + inet before sudo removal (plan §5 — deploy is ungated)
        netmgr.wrappers.deploy()
    except (subprocess.SubprocessError, RuntimeError, OSError,
            netmgr.wrappers.WrapperError) as e:
        raise _EnableError(
            f"network configuration failed ({e})\n"
            "  Run: ark abort --now to roll back (gate still armed)"
        ) from None

    opslog.set_step("Mode write")
    mode.write("focused")
    if mode.read() != "focused":
        raise _EnableError(
            f"failed to verify mode write — check {ARK_DATA_DIR}/mode\n"
            "  Run: ark abort --now to roll back (gate still armed)"
        )
    opslog.ok("mode = focused")

    opslog.set_step("Sudo removal")
    if not deluser_sudo():
        raise _EnableError(
            "failed to remove user from sudo group\n"
            "  Run: ark abort --now to roll back (gate still armed)"
        )
    opslog.ok("user removed from sudo group")

    _reboot_tail()


# ── Disable helpers ───────────────────────────────────────────────────────────


class _DisableError(RuntimeError):
    """Fatal disable failure with a user-facing message."""


def _shadow_root_hash() -> str | None:
    """Current root password hash from /etc/shadow (None when absent/locked)."""
    try:
        with open("/etc/shadow") as f:
            for line in f:
                if line.startswith("root:"):
                    hash_ = line.strip().split(":")[1]
                    if hash_ in ("", "!", "*", "!*"):
                        return None
                    return hash_
    except OSError:
        return None
    return None


def _usermod_password_fallback() -> None:
    """usermod -p <openssl -6 hash> fallback when chpasswd -c fails."""
    try:
        h = subprocess.run(
            ["openssl", "passwd", "-6", DISABLE_ROOT_PASSWORD],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise _DisableError(
            f"root password reset failed: openssl hash generation failed ({e})\n"
            "  Recovery: sudo passwd root, then re-run: ark disable"
        ) from None
    hash_ = h.stdout.strip()
    if not hash_:
        raise _DisableError(
            "root password reset failed: openssl produced an empty hash\n"
            "  Recovery: sudo passwd root, then re-run: ark disable"
        )
    r = subprocess.run(
        ["usermod", "-p", hash_, "root"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if r.returncode != 0:
        raise _DisableError(
            f"root password reset failed: usermod -p failed "
            f"({r.stderr.strip() or '(no stderr)'})\n"
            "  Recovery: sudo passwd root, then re-run: ark disable"
        )


def _reset_root_password() -> None:
    """Reset root to DISABLE_ROOT_PASSWORD; verify the shadow hash changed.

    chpasswd -c YESCRYPT bypasses pam_unix obscure (a trivial password would
    be rejected otherwise); on failure fall back to usermod -p with an openssl
    -6 hash. system.cask is kept — the old random password goes stale
    (harmless; a re-enable overwrites it).
    """
    before = _shadow_root_hash()
    try:
        r = subprocess.run(
            ["chpasswd", "-c", "YESCRYPT"],
            input=f"root:{DISABLE_ROOT_PASSWORD}",
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        opslog.warn(f"chpasswd -c YESCRYPT raised: {e} — trying usermod fallback")
        _usermod_password_fallback()
    else:
        if r.returncode != 0:
            opslog.warn(
                f"chpasswd -c YESCRYPT failed: {r.stderr.strip() or '(no stderr)'}"
                " — trying usermod fallback"
            )
            _usermod_password_fallback()

    after = _shadow_root_hash()
    if after is None or after == before:
        raise _DisableError(
            "root password reset did not verify in /etc/shadow (hash "
            "unchanged or locked)\n"
            "  Recovery: sudo passwd root, then re-run: ark disable"
        )
    opslog.ok("root password reset — /etc/shadow hash changed")


def _format_remaining(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days} days, {hours} hours, {minutes} minutes"
    if hours > 0:
        return f"{hours} hours, {minutes} minutes"
    return f"{minutes} minutes"


def _tle_not_expired(unlock_ts: int) -> _DisableError:
    now = int(time.time())
    remaining = max(unlock_ts - now, 0)
    return _DisableError(
        "Timelock has NOT expired yet — cannot disable\n"
        f"  Will be available at: "
        f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(unlock_ts))} "
        f"({_format_remaining(remaining)} from now)\n"
        "  Wait for the timelock to expire, then re-run: ark disable"
    )


def _tle_gate() -> None:
    """Strict fail-closed TLE gate (ark-disable-flow §1 step 6).

    Proof-only: tle -d to /dev/null — success means the timer elapsed, and no
    plaintext root password ever touches disk. A failed decrypt must be
    bounded by drand (round N) or cask metadata; if neither can bound the
    timer the gate refuses — the gate is never silently skipped.
    """
    cask_path = os.path.join(lib.CASK_DIR, "system.cask")
    if not os.path.isfile(cask_path):
        raise netmgr.guards.PrereqError(
            f"system.cask not found: {cask_path}\n"
            "  Run 'ark enable' to cask system credentials"
        )
    try:
        tle_bin = netmgr.guards.find_tle()
    except netmgr.guards.PrereqError as e:
        raise _DisableError(str(e)) from None

    r = subprocess.run(
        [tle_bin, "-d", "-o", "/dev/null", cask_path],
        capture_output=True, text=True, timeout=TLE_TIMEOUT, check=False,
    )
    if r.returncode == 0:
        opslog.ok("system.cask decrypts — TLE timer elapsed")
        return

    now = int(time.time())
    unlock_ts: int | None = None

    match = re.search(r"round (\d+)", r.stderr)
    if match and lib._load_drand_cache():
        round_num = int(match.group(1))
        unlock_ts = (
            lib.DRAND_CACHE["genesis"] + (round_num - 1) * lib.DRAND_CACHE["period"]
        )

    if unlock_ts is None:
        meta = lib._load_cask_metadata()
        if meta is not None:
            unlock_ts = (
                meta["cask_timestamp"] + lib.parse_duration(meta["tle_duration"])
            )

    if unlock_ts is None:
        raise netmgr.guards.PrereqError(
            "cannot determine timelock state — tle failed and neither drand "
            "nor cask metadata can bound the timer\n"
            "  Run: ark abort --now to roll back, or inspect "
            "/opt/ark/cask/metadata.json"
        )

    if unlock_ts > now:
        raise _tle_not_expired(unlock_ts)
    opslog.ok("TLE timer elapsed")


# ── Subcommands ───────────────────────────────────────────────────────────────

def _get_user() -> str:
    return os.getenv("SUDO_USER") or os.getenv("USER") or "user"


def cmd_disable() -> None:
    """Focused → unrestricted (ark-disable-flow.md)."""
    try:
        _run_disable()
    except _DisableError as e:
        opslog.error(str(e))
        opslog.end_session("disable", "FAILED")
        sys.exit(f"Error: {e}")
    except (netmgr.guards.PrereqError, netmgr.guards.NetworkError) as e:
        opslog.error(str(e))
        opslog.end_session("disable", "FAILED")
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        opslog.end_session("disable", "FAILED")
        print("\nCancelled.", file=sys.stderr)
        sys.exit(0)


def _run_disable() -> None:
    user = _get_user()

    # ── Phase 1 — Preflight (no state mutation) ───────────────────────────────
    opslog.set_step("Preflight")
    netmgr.guards.check_lockdown_dir()
    opslog.ok("lockdown dir present")
    netmgr.guards.check_scripts()
    opslog.ok("scripts executable")
    mode.ensure()
    current = mode.read()

    if current not in ("unrestricted", "focused", "locked"):
        raise _DisableError(
            f"invalid mode '{current}' — run: mode.py write unrestricted"
        )
    if current == "unrestricted":
        print("Already unrestricted")
        opslog.end_session("disable", "OK")
        sys.exit(0)
    if current == "locked":
        raise _DisableError(
            "cannot disable from locked mode\n"
            "  Handled by the lock-timer expiry (locked → focused, then "
            "re-run ark disable) or: ark abort --now"
        )

    if Path(STATE_FILE).is_file():
        opslog.warn(
            "stale enable.json present — an interrupted enable left the abort "
            "gate armed; it will be cleared on a successful disable"
        )

    opslog.set_step("TLE gate")
    _tle_gate()

    # ── Phase 2 — Credential recovery + root reset (first mutations) ─────────
    opslog.set_step("Root password reset")
    _reset_root_password()

    opslog.set_step("Sudo restore")
    if not adduser_sudo():
        raise _DisableError(
            "failed to restore sudo group\n"
            "  Recovery: sudo adduser $USER sudo, then re-run: ark disable"
        )
    opslog.ok(f"sudo restored for {user}")

    # ── Phase 3 — Transition (rollback to focused on mode mismatch) ──────────
    opslog.set_step("Network transition")
    try:
        netmgr.policies.deploy()
        netmgr.dns.configure("unrestricted")
        netmgr.firewall.apply("unrestricted")
    except (subprocess.SubprocessError, RuntimeError, OSError,
            netmgr.wrappers.WrapperError) as e:
        raise _DisableError(
            f"network configuration failed ({e})\n"
            "  Re-run: ark disable (root password and sudo are already "
            "restored)"
        ) from None

    opslog.set_step("Mode write")
    mode.write("unrestricted")
    if mode.read() != "unrestricted":
        try:
            netmgr.dns.configure("focused")
            netmgr.firewall.apply("focused")
        except (subprocess.SubprocessError, RuntimeError, OSError,
                netmgr.wrappers.WrapperError) as e:
            opslog.error(f"rollback to focused failed ({e})")
        raise _DisableError(
            f"failed to verify mode write — check {ARK_DATA_DIR}/mode\n"
            "  Re-run: ark disable"
        )
    opslog.ok("mode = unrestricted")

    _delete_state()
    opslog.ok("enable.json cleared — abort gate disarmed")
    opslog.end_session("disable", "OK")

    # Unreachable on success — the reboot kills the process. A return means
    # the reboot silently failed; SystemExit from lib.reboot()'s failure path
    # propagates (its own end_session FAILED already ran).
    lib.reboot()
    opslog.error("reboot command returned without rebooting")
    opslog.error("Please reboot manually — disable is complete")
    sys.exit(1)


def _detect_target_from_locked() -> str:
    casked = os.path.join(lib.CASK_DIR, "system.cask")
    if not os.path.isfile(casked):
        return "unrestricted"
    try:
        tle_bin = netmgr.guards.find_tle()
    except netmgr.guards.PrereqError:
        return "unrestricted"
    r = subprocess.run(
        [tle_bin, "-d", "-o", "/dev/null", casked],
        capture_output=True, text=True, timeout=TLE_TIMEOUT, check=False,
    )
    return "unrestricted" if r.returncode == 0 else "focused"


# ── Lock timer ───────────────────────────────────────────────────────────────

TRANSITION_SCRIPT = f"{ARK_DATA_DIR}/scripts/ark-transition.sh"
TIMER_SERVICE = "/etc/systemd/system/ark-transition.service"
TIMER_UNIT = "/etc/systemd/system/ark-transition.timer"


def setup_lock_timer(duration_secs: int) -> None:
    script_content = """#!/bin/bash
set -euo pipefail
MODE=$(python3 /opt/ark/scripts/mode.py read)
if [ "$MODE" != "locked" ]; then
    exit 0
fi
python3 /opt/ark/scripts/mode.py write focused
python3 /opt/ark/scripts/netmgr.py configure focused
sleep 10
shutdown -r now "lockdown timer expired"
"""
    with open(TRANSITION_SCRIPT, "w") as f:
        f.write(script_content)
    os.chmod(TRANSITION_SCRIPT, 0o755)
    os.chown(TRANSITION_SCRIPT, 0, 0)
    try:
        immutable_lib.set_immutable(TRANSITION_SCRIPT)
    except immutable_lib.ImmutableError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("  Lock proceeds without transition-script immutability — "
              + "re-running 'ark lock' re-applies it.", file=sys.stderr)

    service_content = """[Unit]
Description=Ark mode transition
After=network.target

[Service]
Type=oneshot
ExecStart=/opt/ark/scripts/ark-transition.sh
"""
    with open(TIMER_SERVICE, "w") as f:
        f.write(service_content)

    timer_content = f"""[Unit]
Description=Ark lock expiry timer

[Timer]
OnActiveSec={duration_secs}
Persistent=true

[Install]
WantedBy=timers.target
"""
    with open(TIMER_UNIT, "w") as f:
        f.write(timer_content)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "ark-transition.timer"],
                   check=True)
    print(f"Lock timer set for {lib.format_duration(duration_secs)}")


def cancel_lock_timer() -> None:
    subprocess.run(["systemctl", "stop", "ark-transition.timer"],
                   capture_output=True, check=False)
    subprocess.run(["systemctl", "disable", "ark-transition.timer"],
                   capture_output=True, check=False)
    for path in [TIMER_UNIT, TIMER_SERVICE]:
        if os.path.exists(path):
            immutable_lib.clear_immutable(path, strict=False)
            try:
                os.remove(path)
            except PermissionError:
                print(f"Warning: could not remove {path} — run: "
                      f"sudo chattr -i {path} && sudo rm {path}",
                      file=sys.stderr)
    if os.path.exists(TRANSITION_SCRIPT):
        immutable_lib.clear_immutable(TRANSITION_SCRIPT, strict=False)
        try:
            os.remove(TRANSITION_SCRIPT)
        except PermissionError:
            print(f"Warning: could not remove {TRANSITION_SCRIPT} — run: "
                  f"sudo chattr -i {TRANSITION_SCRIPT} && "
                  f"sudo rm {TRANSITION_SCRIPT}", file=sys.stderr)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, check=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def cmd_abort(args: argparse.Namespace) -> None:
    """Delegate to abort.py — it owns its opslog session (abort.log)."""
    sys.exit(abort.run(args))


# ── Logs ───────────────────────────────────────────────────────────────────────

LOG_COMMANDS: tuple[str, ...] = ("enable", "disable", "abort")


def cmd_logs(args: argparse.Namespace) -> None:
    """List per-command logs, or print the latest run of one command.

    Each per-command log is opened with mode="w", so the file already holds
    only the latest run. Read-only: no opslog session markers (configuring
    opslog here would truncate the very log being read).
    """
    logs_dir = Path(f"{ARK_DATA_DIR}/logs")
    if not args.log_cmd:
        found = False
        for name in LOG_COMMANDS:
            path = logs_dir / f"{name}.log"
            if not path.is_file():
                continue
            found = True
            stat = path.stat()
            mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(stat.st_mtime))
            print(f"{name:<8} {path}  ({stat.st_size} bytes, {mtime})")
        if not found:
            print("No ark command logs yet — run: ark enable / ark disable "
                  "/ ark abort --now")
        return

    path = logs_dir / f"{args.log_cmd}.log"
    if not path.is_file():
        sys.exit(f"Error: no {args.log_cmd}.log yet — run: ark {args.log_cmd}")
    try:
        sys.stdout.write(path.read_text())
    except OSError as e:
        sys.exit(f"Error: cannot read {path}: {e}")


def main() -> None:
    if not sys.stdin.isatty():
        sys.exit("Error: ark requires an interactive terminal")

    parser = argparse.ArgumentParser(description="Ark — internet lockdown CLI")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("enable", help="Enable focused mode")
    subs.add_parser("disable", help="Disable focused mode")
    abort_parser = subs.add_parser(
        "abort", help="Roll back an incomplete enable (snapshot restore)"
    )
    abort_parser.add_argument(
        "--now",
        action="store_true",
        help="Proceed immediately (TTY-bound; no scheduled/headless abort)",
    )
    logs_parser = subs.add_parser("logs", help="Show command logs")
    logs_parser.add_argument(
        "log_cmd",
        nargs="?",
        choices=LOG_COMMANDS,
        help="Command whose latest run to print (default: list all)",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # abort and logs bypass the generic opslog session below: abort configures
    # abort.log itself, and logs is read-only (configuring opslog would
    # truncate the very log it reads — mode="w").
    if args.command == "abort":
        cmd_abort(args)
    if args.command == "logs":
        cmd_logs(args)
        sys.exit(0)

    opslog.configure("ark", file=f"{ARK_DATA_DIR}/logs/{args.command}.log",
                     mode="w")
    opslog.session(args.command)
    try:
        {"enable": cmd_enable, "disable": cmd_disable}[args.command]()
    except KeyboardInterrupt:
        print("\nCancelled.")
        opslog.end_session(args.command, "FAILED")
        sys.exit(0)
    except CaskError as e:
        opslog.end_session(args.command, "FAILED")
        sys.exit(f"Error: {e}")
    opslog.end_session(args.command, "OK")


if __name__ == "__main__":
    main()
