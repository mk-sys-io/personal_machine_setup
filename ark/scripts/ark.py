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
import os
import subprocess
import sys
import threading

if os.geteuid() != 0:
    sys.exit("Error: ark requires root\n  Run: sudo ark <command>")

# cask_lib, mode, netmgr, and opslog live in /opt/ark/scripts/
sys.path.insert(0, "/opt/ark/scripts")
import abort
import cask_lib as lib
import cask_system
import immutable_lib
import mode
import netmgr
import opslog
from cask_lib import CaskError

# ── Constants ─────────────────────────────────────────────────────────────────

ARK_DATA_DIR = "/opt/ark"
TLE_TIMEOUT = 300


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


# ── Subcommands ───────────────────────────────────────────────────────────────

def _get_user() -> str:
    return os.getenv("SUDO_USER") or os.getenv("USER") or "user"


def cmd_enable() -> None:
    require(netmgr.guards.check_lockdown_dir,
            msg="Error: lockdown data directory not found\n"
                "  Run: sudo install.sh")
    require(netmgr.guards.audit_package_managers,
            msg="Error: package managers detected — cannot proceed")
    require(netmgr.guards.check_scripts,
            msg="Error: netmgr.py missing or not executable")
    require(netmgr.guards.check_blocklist,
            msg="Error: blocklist.dnsmasq.conf missing — run: netmgr generate")

    try:
        tle_bin = netmgr.guards.check_prereqs()
    except (netmgr.guards.PrereqError, netmgr.guards.NetworkError) as e:
        sys.exit(f"Error: {e}")

    mode.ensure()
    current = mode.read()
    if current != "unrestricted":
        sys.exit(f"Error: cannot enable from {current} mode")

    user = _get_user()
    print("\n")
    print("  Enable Focus Mode")
    print("\n")
    print("  State: unrestricted -> focused")
    print("\n")
    print("  This will:")
    print(f"    - Remove sudo access for {user}")
    print("    - Deploy browser policies (Brave, Chromium, Chrome, Firefox)")
    print("    - Deploy bookmarks")
    print("    - Configure dnsmasq allowlist (focused)")
    print("    - Apply nftables firewall rules (focused)")
    print("    - Cask system credentials with TLE")
    print("    - Reboot")
    print("\n")

    try:
        ans = input("  Proceed? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)
    if ans.strip().lower() not in ("y", "yes"):
        sys.exit("Aborted.")

    print("\n")

    try:
        duration = lib.prompt_duration()
        cask_system.cask_credentials(tle_bin, duration)
    except CaskError as e:
        sys.exit(f"Error: cask failed ({e})\n"
                 "  System state unchanged. Re-run: ark enable")

    try:
        netmgr.policies.deploy()
        netmgr.dns.configure("focused")
        netmgr.firewall.apply("focused")
        # Rebuild shims + inet before sudo removal (plan §5 — deploy is ungated)
        netmgr.wrappers.deploy()
    except (subprocess.SubprocessError, RuntimeError, OSError,
            netmgr.wrappers.WrapperError) as e:
        sys.exit(f"Error: network configuration failed ({e})")

    mode.write("focused")
    if mode.read() != "focused":
        sys.exit("Error: failed to verify mode write — check "
                 f"{ARK_DATA_DIR}/mode")

    deluser_sudo()

    try:
        lib.reboot()
    except SystemExit:
        pass
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Warning: reboot failed ({e})", file=sys.stderr)
        print("Please reboot manually.", file=sys.stderr)
        sys.exit(1)


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
