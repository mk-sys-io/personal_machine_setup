#!/usr/bin/env python3
"""Aegis — internet lockdown CLI.

Usage:
  aegis enable     Enable focused mode (blocklist + seal)
  aegis disable    Disable focused/locked mode
  aegis lock       Lock system (focused + sealed + locked config)

Requires root. Deployed to /usr/local/bin/aegis (root:root 755).
"""

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import threading

# seal_lib and mode live in /opt/lockdown/scripts/
sys.path.insert(0, "/opt/lockdown/scripts")
import mode  # noqa: E402
import seal_lib as lib  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

LOCKDOWN_DATA_DIR = "/opt/lockdown"
GENERATE_DNSMASQ = f"{LOCKDOWN_DATA_DIR}/scripts/generate-dnsmasq.sh"
GENERATE_NFTABLES = f"{LOCKDOWN_DATA_DIR}/scripts/generate-nftables.sh"
GENERATE_POLICIES = f"{LOCKDOWN_DATA_DIR}/scripts/generate-policies.sh"


# ── Sudo keepalive ────────────────────────────────────────────────────────────

_stop_keepalive = threading.Event()


def _keepalive():
    while not _stop_keepalive.is_set():
        try:
            subprocess.run(["sudo", "-v"], capture_output=True, timeout=10)
        except Exception:
            pass
        _stop_keepalive.wait(60)


_keepalive_thread = threading.Thread(target=_keepalive, daemon=True)
_keepalive_thread.start()
atexit.register(_stop_keepalive.set)


# ── Precondition wrapper ──────────────────────────────────────────────────────

def require(check, *args, msg=None):
    if not check(*args):
        sys.exit(msg or f"Error: {check.__name__} check failed")


# ── Subprocess runner ─────────────────────────────────────────────────────────

def run(script: str, *args: str, timeout: int = 300) -> None:
    cmd = [script, *args]
    try:
        result = subprocess.run(cmd, text=True, timeout=timeout)
    except FileNotFoundError:
        sys.exit(f"Error: script not found: {script}")
    except subprocess.TimeoutExpired:
        sys.exit(f"Error: {' '.join(cmd)} timed out after {timeout}s")
    if result.returncode != 0:
        sys.exit(f"Error: {' '.join(cmd)} failed (exit {result.returncode})")


# ── Check functions ───────────────────────────────────────────────────────────

def check_lockdown_dir() -> bool:
    if not os.path.isdir(LOCKDOWN_DATA_DIR):
        return False
    for name in ["scripts", "nftables.conf.base", "nftables.conf.restricted"]:
        if not os.path.exists(f"{LOCKDOWN_DATA_DIR}/{name}"):
            return False
    return True


def check_scripts() -> bool:
    for script in [GENERATE_POLICIES, GENERATE_DNSMASQ, GENERATE_NFTABLES]:
        if not os.access(script, os.X_OK):
            return False
    return True


def check_blocklist_hosts() -> bool:
    return os.path.isfile(f"{LOCKDOWN_DATA_DIR}/domains/blocklist.hosts")


def audit_package_managers() -> bool:
    blockers = ["flatpak", "snap", "nix"]
    found = [name for name in blockers if shutil.which(name)]
    extra_paths = [
        "/snap/bin/flatpak",
        "/snap/bin/snap",
        "/nix/var/nix/profiles/default/bin/nix",
    ]
    found += [p for p in extra_paths if os.path.isfile(p)]
    return len(found) == 0


# ── Sudo gate helpers ─────────────────────────────────────────────────────────

def deluser_sudo() -> bool:
    user = os.getenv("SUDO_USER") or os.getenv("USER")
    if user is None:
        sys.exit("Error: cannot determine current user")
    result = subprocess.run(["groups", user], capture_output=True, text=True)
    if "sudo" not in result.stdout:
        return True
    subprocess.run(["deluser", user, "sudo"], check=False)
    result = subprocess.run(["groups", user], capture_output=True, text=True)
    return "sudo" not in result.stdout


def adduser_sudo() -> bool:
    user = os.getenv("SUDO_USER") or os.getenv("USER")
    if user is None:
        sys.exit("Error: cannot determine current user")
    result = subprocess.run(["groups", user], capture_output=True, text=True)
    if "sudo" in result.stdout:
        return True
    subprocess.run(["adduser", user, "sudo"], check=False)
    result = subprocess.run(["groups", user], capture_output=True, text=True)
    return "sudo" in result.stdout


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_enable() -> None:
    require(check_lockdown_dir,
            msg="Error: lockdown data directory not found\n"
                "  Run: sudo install.sh")
    require(audit_package_managers,
            msg="Error: package managers detected — cannot proceed")
    require(check_scripts,
            msg="Error: generate scripts missing or not executable")
    require(check_blocklist_hosts,
            msg=f"Error: blocklist.hosts not found at "
                f"{LOCKDOWN_DATA_DIR}/domains/blocklist.hosts\n"
                "  Run: sudo blocklist generate")

    mode.ensure()
    current = mode.read()
    if current != "unrestricted":
        sys.exit(f"Error: cannot enable from {current} mode")

    require(deluser_sudo,
            msg="Error: failed to remove sudo group")

    run(GENERATE_POLICIES)
    run(GENERATE_DNSMASQ, "focused")
    run(GENERATE_NFTABLES, "focused")

    lib.seal_credentials()

    mode.write("focused")
    if mode.read() != "focused":
        sys.exit("Error: failed to verify mode write — check "
                 f"{LOCKDOWN_DATA_DIR}/mode")

    try:
        lib.reboot()
    except SystemExit:
        pass
    except Exception as e:
        print(f"Warning: reboot failed ({e})", file=sys.stderr)
        print("Please reboot manually.", file=sys.stderr)
        sys.exit(1)


def cmd_disable() -> None:
    sys.exit("Error: disable not yet implemented (Phase 1, step 11)")


def cmd_lock() -> None:
    sys.exit("Error: lock not yet implemented (Phase 1, step 12)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not sys.stdin.isatty():
        sys.exit("Error: aegis requires an interactive terminal")

    if os.geteuid() != 0:
        sys.exit("Error: aegis requires root")

    parser = argparse.ArgumentParser(description="Aegis — internet lockdown CLI")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("enable", help="Enable focused mode")
    subs.add_parser("disable", help="Disable focused/locked mode")
    subs.add_parser("lock", help="Lock system")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        {"enable": cmd_enable, "disable": cmd_disable, "lock": cmd_lock}[
            args.command
        ]()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)


if __name__ == "__main__":
    main()
