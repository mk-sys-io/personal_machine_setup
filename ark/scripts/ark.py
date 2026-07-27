#!/usr/bin/env python3
"""Ark — internet lockdown CLI.

Usage:
  ark enable     Enable focused mode (blocklist + seal)
  ark disable    Disable focused/locked mode
  ark lock       Lock system (focused + sealed + locked config)

Requires root. Deployed to /usr/local/bin/ark (root:root 755).

State mutation note: the enable/lock subcommands apply changes in a
specific order (seal first, network configs second, sudo removal last).
If any step fails mid-sequence, the system may be in a partial state.
A timeshift-based rollback mechanism is out of scope for Phase 1.
"""

import argparse
import atexit
import os
import shutil
import subprocess
import sys
import threading
import time

if os.geteuid() != 0:
    sys.exit("Error: ark requires root\n  Run: sudo ark <command>")

# seal_lib and mode live in /opt/ark/scripts/
sys.path.insert(0, "/opt/ark/scripts")
import mode
import seal_lib as lib
from seal_lib import SealError

# ── Constants ─────────────────────────────────────────────────────────────────

ARK_DATA_DIR = "/opt/ark"
GENERATE_DNSMASQ = f"{ARK_DATA_DIR}/scripts/generate-dnsmasq.sh"
GENERATE_NFTABLES = f"{ARK_DATA_DIR}/scripts/generate-nftables.sh"
GENERATE_POLICIES = f"{ARK_DATA_DIR}/scripts/generate-policies.sh"
ALLOWLIST_DIR = ARK_DATA_DIR
ALLOWLIST_FILES = ["infra.txt", "base.txt", "session.txt"]
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
    if not check(*args):
        sys.exit(msg or f"Error: {check.__name__} check failed")
    print(f"  ✓ {check.__name__}")


# ── Subprocess runner ─────────────────────────────────────────────────────────

def run(script: str, *args: str, timeout: int = 300) -> None:
    cmd = [script, *args]
    try:
        result = subprocess.run(cmd, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        sys.exit(f"Error: script not found: {script}")
    except subprocess.TimeoutExpired:
        sys.exit(f"Error: {' '.join(cmd)} timed out after {timeout}s")
    if result.returncode != 0:
        sys.exit(f"Error: {' '.join(cmd)} failed (exit {result.returncode})")


# ── Check functions ───────────────────────────────────────────────────────────

def check_lockdown_dir() -> bool:
    if not os.path.isdir(ARK_DATA_DIR):
        return False
    for name in ["scripts", "nftables.conf.base", "nftables.conf.restricted"]:
        if not os.path.exists(f"{ARK_DATA_DIR}/{name}"):
            return False
    return True


def check_scripts() -> bool:
    for script in [GENERATE_POLICIES, GENERATE_DNSMASQ, GENERATE_NFTABLES]:
        if not os.access(script, os.X_OK):
            return False
    return True


def check_blocklist_hosts() -> bool:
    return os.path.isfile(f"{ARK_DATA_DIR}/domains/blocklist.hosts")


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


def check_allowlist_nonempty() -> bool:
    for name in ALLOWLIST_FILES:
        if lib.count_domains(os.path.join(ALLOWLIST_DIR, name)) > 0:
            return True
    return False


def _find_tle() -> str | None:
    for path in ["/usr/local/bin/tle", "/home/mike/go/bin/tle"]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


DRAND_HOST = "{{ .Env.DRAND_HOST }}"


def check_seal_prereqs() -> str:
    """Verify seal prerequisites before confirmation. Returns tle path."""
    tle_bin = _find_tle()
    if not tle_bin:
        sys.exit("Error: tle binary not found\n"
                 "  Install: go install github.com/drand/tle/cmd/tle@latest")

    try:
        subprocess.run(
            ["timeout", "5", "getent", "hosts", DRAND_HOST],
            capture_output=True, check=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        print("  Checking drand DNS (retrying)...", file=sys.stderr)
        time.sleep(3)
        try:
            subprocess.run(
                ["timeout", "5", "getent", "hosts", DRAND_HOST],
                capture_output=True, check=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            sys.exit("Error: cannot reach drand network (DNS failed)\n"
                     "  Check your internet connection")

    try:
        subprocess.run(
            ["timeout", "5", "bash", "-c",
             f"echo > /dev/tcp/{DRAND_HOST}/443"],
            capture_output=True, check=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        sys.exit("Error: cannot reach drand network (TCP failed)\n"
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

    cred_path = os.path.join(lib.SEAL_WORK_DIR, "system.credentials")
    if not os.path.isfile(cred_path):
        sys.exit(f"Error: {cred_path} not found\n"
                 "  Run seal first or create the credentials file")

    if not shutil.which("openssl"):
        sys.exit("Error: openssl not found")

    if not shutil.which("chpasswd"):
        sys.exit("Error: chpasswd not found")

    return tle_bin


# ── Warn-on-failure runner ────────────────────────────────────────────────────

def run_or_warn(script: str, *args: str, timeout: int = 300) -> bool:
    cmd = [script, *args]
    try:
        result = subprocess.run(cmd, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        print(f"  Warning: script not found: {script}", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print(f"  Warning: {' '.join(cmd)} timed out after {timeout}s",
              file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"  Warning: {' '.join(cmd)} failed (exit {result.returncode})",
              file=sys.stderr)
        return False
    return True


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
    require(check_lockdown_dir,
            msg="Error: lockdown data directory not found\n"
                "  Run: sudo install.sh")
    require(audit_package_managers,
            msg="Error: package managers detected — cannot proceed")
    require(check_scripts,
            msg="Error: generate scripts missing or not executable")
    hosts_file = os.path.join(ARK_DATA_DIR, "domains", "blocklist.hosts")
    if not os.path.isfile(hosts_file):
        print("  blocklist.hosts missing — generating from source files...")
        run("blocklist", "generate")
    else:
        print("  ✓ blocklist.hosts present")

    check_seal_prereqs()

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
    print("    - Seal system credentials with TLE")
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
        lib.seal_credentials()
    except SealError as e:
        sys.exit(f"Error: seal failed ({e})\n"
                 "  System state unchanged. Re-run: ark enable")

    run_or_warn(GENERATE_POLICIES)
    run_or_warn(GENERATE_DNSMASQ, "focused")
    run_or_warn(GENERATE_NFTABLES, "focused")

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
    require(check_lockdown_dir,
            msg=f"Error: lockdown data directory not found at {ARK_DATA_DIR}\n"
                "  Run: sudo install.sh")
    require(check_scripts,
            msg="Error: generate scripts missing or not executable")
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

    run(GENERATE_POLICIES)
    run(GENERATE_DNSMASQ, target)
    run(GENERATE_NFTABLES, target)

    mode.write(target)
    if mode.read() != target:
        run(GENERATE_DNSMASQ, current)
        run(GENERATE_NFTABLES, current)
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
    sealed = os.path.join(lib.SEAL_DIR, "system.sealed")
    if not os.path.isfile(sealed):
        return "unrestricted"
    tle_bin = _find_tle()
    if not tle_bin:
        return "unrestricted"
    r = subprocess.run(
        [tle_bin, "-d", "-o", "/dev/null", sealed],
        capture_output=True, text=True, timeout=TLE_TIMEOUT, check=False,
    )
    return "unrestricted" if r.returncode == 0 else "focused"


def cmd_lock() -> None:
    require(check_lockdown_dir,
            msg="Error: lockdown data directory not found\n"
                "  Run: sudo install.sh")
    require(audit_package_managers,
            msg="Error: package managers detected — cannot proceed")
    require(check_scripts,
            msg="Error: generate scripts missing or not executable")
    require(check_allowlist_nonempty,
            msg="Error: allowlist is empty — add domains to "
                "infra.txt/base.txt/session.txt")

    mode.ensure()
    current = mode.read()
    if current == "locked":
        sys.exit("Error: already locked")
    if current == "unrestricted":
        sys.exit("Error: cannot lock from unrestricted mode.\n"
                 "  Run 'ark enable' first.")

    tle_bin = _find_tle()
    if not tle_bin:
        sys.exit("Error: tle binary not found\n"
                 "  Install: go install github.com/drand/tle/cmd/tle@latest")
    sealed = os.path.join(lib.SEAL_DIR, "system.sealed")
    try:
        remaining = lib.get_remaining_tle_time(tle_bin, sealed)
    except SealError as e:
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

    run(GENERATE_POLICIES)
    run(GENERATE_DNSMASQ, "locked")
    run(GENERATE_NFTABLES, "locked")
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
    script_content = f"""#!/bin/bash
set -euo pipefail
MODE=$(python3 /opt/ark/scripts/mode.py read)
if [ "$MODE" != "locked" ]; then
    exit 0
fi
python3 /opt/ark/scripts/mode.py write focused
{ARK_DATA_DIR}/scripts/generate-dnsmasq.sh focused
{ARK_DATA_DIR}/scripts/generate-nftables.sh focused
{ARK_DATA_DIR}/scripts/generate-policies.sh
sleep 10
shutdown -r now "lockdown timer expired"
"""
    with open(TRANSITION_SCRIPT, "w") as f:
        f.write(script_content)
    os.chmod(TRANSITION_SCRIPT, 0o755)
    os.chown(TRANSITION_SCRIPT, 0, 0)
    subprocess.run(["chattr", "+i", TRANSITION_SCRIPT], capture_output=True, check=False)

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
            subprocess.run(["chattr", "-i", path], capture_output=True, check=False)
            try:
                os.remove(path)
            except PermissionError:
                print(f"Warning: could not remove {path} — run: "
                      f"sudo chattr -i {path} && sudo rm {path}",
                      file=sys.stderr)
    if os.path.exists(TRANSITION_SCRIPT):
        subprocess.run(["chattr", "-i", TRANSITION_SCRIPT], capture_output=True, check=False)
        try:
            os.remove(TRANSITION_SCRIPT)
        except PermissionError:
            print(f"Warning: could not remove {TRANSITION_SCRIPT} — run: "
                  f"sudo chattr -i {TRANSITION_SCRIPT} && "
                  f"sudo rm {TRANSITION_SCRIPT}", file=sys.stderr)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, check=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not sys.stdin.isatty():
        sys.exit("Error: ark requires an interactive terminal")

    parser = argparse.ArgumentParser(description="Ark — internet lockdown CLI")
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
    except SealError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
