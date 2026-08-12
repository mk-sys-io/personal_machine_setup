#!/usr/bin/env python3
"""Ark — internet lockdown CLI.

Usage:
  ark enable     Enable focused mode (blocklist + cask)
  ark disable    Disable focused/locked mode
  ark lock       Lock system (focused + casked + locked config)
  ark abort --now  Roll back an incomplete enable (snapshot restore)

Requires root. Deployed to /usr/local/bin/ark (root:root 755).

State mutation note: the enable/lock subcommands apply changes in a
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


# ── Subcommands ───────────────────────────────────────────────────────────────

def _get_user() -> str:
    return os.getenv("SUDO_USER") or os.getenv("USER") or "user"


def cmd_disable() -> None:
    require(netmgr.guards.check_lockdown_dir,
            msg=f"Error: lockdown data directory not found at {ARK_DATA_DIR}\n"
                "  Run: sudo install.sh")
    require(netmgr.guards.check_scripts,
            msg="Error: netmgr.py missing or not executable")
    mode.ensure()
    current = mode.read()

    if current not in ("unrestricted", "focused", "locked"):
        sys.exit(f"Error: invalid mode '{current}' — run: "
                 "mode.py write unrestricted")
    if current == "unrestricted":
        print("Already unrestricted")
        return

    if current == "locked":
        target = _detect_target_from_locked()
    else:
        target = "unrestricted"

    require(adduser_sudo,
            msg="Error: failed to restore sudo group\n"
                "  Recovery: sudo adduser $USER sudo")

    if current == "locked":
        cancel_lock_timer()

    try:
        netmgr.policies.deploy()
        netmgr.dns.configure(target)
        netmgr.firewall.apply(target)
    except (subprocess.SubprocessError, RuntimeError, OSError) as e:
        sys.exit(f"Error: network configuration failed ({e})")

    mode.write(target)
    if mode.read() != target:
        try:
            netmgr.dns.configure(current)
            netmgr.firewall.apply(current)
        except (subprocess.SubprocessError, RuntimeError, OSError) as e:
            print(f"Warning: rollback to {current} failed ({e})",
                  file=sys.stderr)
        sys.exit(f"Error: failed to verify mode write — check "
                 f"{ARK_DATA_DIR}/mode")

    try:
        lib.reboot()
    except SystemExit:
        pass
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Warning: reboot failed ({e})", file=sys.stderr)
        print("Please reboot manually.", file=sys.stderr)
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


def cmd_lock() -> None:
    require(netmgr.guards.check_lockdown_dir,
            msg="Error: lockdown data directory not found\n"
                "  Run: sudo install.sh")
    require(netmgr.guards.audit_package_managers,
            msg="Error: package managers detected — cannot proceed")
    require(netmgr.guards.check_scripts,
            msg="Error: netmgr.py missing or not executable")
    require(netmgr.guards.check_allowlist_nonempty,
            msg="Error: allowlist is empty — add domains to "
                "infra.txt/base.txt/session.txt")

    mode.ensure()
    current = mode.read()
    if current == "locked":
        sys.exit("Error: already locked")
    if current == "unrestricted":
        sys.exit("Error: cannot lock from unrestricted mode.\n"
                 "  Run 'ark enable' first.")

    try:
        tle_bin = netmgr.guards.find_tle()
    except netmgr.guards.PrereqError as e:
        sys.exit(f"Error: {e}")
    casked = os.path.join(lib.CASK_DIR, "system.cask")
    try:
        remaining = lib.get_remaining_tle_time(tle_bin, casked)
    except CaskError as e:
        sys.exit(f"Error: {e}")

    try:
        ans = input("Warning: this will lock your system. You will lose "
                    "sudo\nand network access until the lock expires. "
                    "Proceed? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)
    if ans.strip().lower() not in ("y", "yes"):
        sys.exit("Aborted.")

    duration_secs = lib.prompt_lock_duration(remaining)

    try:
        netmgr.policies.deploy()
        netmgr.dns.configure("locked")
        netmgr.firewall.apply("locked")
        # Rebuild shims + inet before mode.write (plan §5 — deploy is ungated)
        netmgr.wrappers.deploy()
    except (subprocess.SubprocessError, RuntimeError, OSError,
            netmgr.wrappers.WrapperError) as e:
        sys.exit(f"Error: network configuration failed ({e})")
    mode.write("locked")
    if mode.read() != "locked":
        sys.exit("Error: failed to verify mode write — check "
                 f"{ARK_DATA_DIR}/mode")

    setup_lock_timer(duration_secs)

    try:
        lib.reboot()
    except SystemExit:
        pass
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Warning: reboot failed ({e})", file=sys.stderr)
        print("Please reboot manually.", file=sys.stderr)
        sys.exit(1)


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


def main() -> None:
    if not sys.stdin.isatty():
        sys.exit("Error: ark requires an interactive terminal")

    parser = argparse.ArgumentParser(description="Ark — internet lockdown CLI")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("enable", help="Enable focused mode")
    subs.add_parser("disable", help="Disable focused/locked mode")
    subs.add_parser("lock", help="Lock system")
    abort_parser = subs.add_parser(
        "abort", help="Roll back an incomplete enable (snapshot restore)"
    )
    abort_parser.add_argument(
        "--now",
        action="store_true",
        help="Proceed immediately (TTY-bound; no scheduled/headless abort)",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # abort manages its own session markers (abort.run configures abort.log);
    # the generic command logging below would double-write START/END markers.
    if args.command == "abort":
        cmd_abort(args)

    opslog.configure("ark", file=f"{ARK_DATA_DIR}/logs/{args.command}.log",
                     mode="w")
    opslog.session(args.command)
    try:
        {"enable": cmd_enable, "disable": cmd_disable, "lock": cmd_lock}[
            args.command
        ]()
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
