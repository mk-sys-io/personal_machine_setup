from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import immutable_lib
import mode
import netmgr
import netmgr.wrappers
import opslog
from cask import lib

from ark import (
    ARK_DATA_DIR,
    DISABLE_ROOT_PASSWORD,
    STATE_FILE,
    TLE_TIMEOUT,
    _delete_state,
    _format_remaining,
    _get_user,
    reboot_with_countdown,
)

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

    NOTE (clock-spoofing): the fallback paths below compare ``time.time()``
    against a drand-derived ``unlock_ts`` — setting the clock forward could
    fool the elapsed-time estimate. Actual decryption stays beacon-bound, and
    in focused mode the user has no sudo to change the clock, so this is not
    exploitable today. When ``ark lock`` is re-added, the locked→focused
    transition timer must NOT trust systemd ``OnActiveSec`` alone — validate
    drand round progression instead of the local wall clock.
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

    try:
        r = subprocess.run(
            [tle_bin, "-d", "-o", "/dev/null", cask_path],
            capture_output=True, text=True, timeout=TLE_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise _DisableError(
            f"tle -d timed out after {TLE_TIMEOUT}s ({e}) — timelock state "
            "unknown, refusing to disable\n"
            "  Run: ark abort to roll back, or inspect "
            "{{ .Env.ARK_DATA_PATH }}/cask/metadata.json"
        ) from None
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
            "  Run: ark abort to roll back, or inspect "
            "{{ .Env.ARK_DATA_PATH }}/cask/metadata.json"
        )

    if unlock_ts > now:
        raise _tle_not_expired(unlock_ts)
    opslog.ok("TLE timer elapsed")


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
    except immutable_lib.ImmutableError as e:
        opslog.error(str(e))
        opslog.end_session("disable", "FAILED")
        sys.exit(f"Error: {e}\n"
                 "  Re-run: ark disable (repair re-applies the flag)")
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
            "re-run ark disable) or: ark abort"
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
    from ark import adduser_sudo
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

    netmgr.guards.capture_package_baseline()
    opslog.ok("package baseline captured")

    _delete_state()
    opslog.ok("enable.json cleared — abort gate disarmed")
    opslog.end_session("disable", "OK")

    reboot_with_countdown()
