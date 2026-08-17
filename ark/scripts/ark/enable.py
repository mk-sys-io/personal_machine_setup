from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import immutable_lib
import mode
import netmgr
import netmgr.blocklist.manage
import netmgr.health
import opslog
from cask import lib, mcask
from cask import system as cask_system
from cask.lib import CaskError

from ark import (
    ARK_DATA_DIR,
    BATTERY_THRESHOLD,
    STATE_FILE,
    TIMESHIFT_SNAPSHOT_PREFIX,
    _delete_state,
    _get_user,
    abort,
    reboot_with_countdown,
)

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


def _snapshots_with_prefix() -> set[str]:
    """Canonical ids of snapshots whose comment is the ark prefix.

    Filters the raw `timeshift --list` output by the prefix comment so a
    concurrent manual `timeshift --create` (no prefix) can never be pinned
    as the abort target; recency only breaks same-prefix ties.
    """
    result = subprocess.run(
        ["timeshift", "--list"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise _EnableError(
            f"timeshift --list failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    rows = [
        line for line in result.stdout.splitlines()
        if TIMESHIFT_SNAPSHOT_PREFIX in line
    ]
    return abort.parse_list("\n".join(rows))


def _snapshot_create() -> str:
    """Create a snapshot with the configured prefix; return the canonical id.

    The just-created snapshot is identified by its prefix comment, not by
    recency — a concurrent manual snapshot can never be picked up as the
    abort target. Canonical format (YYYY-MM-DD HH:MM:SS) sorts lexically and
    matches abort's verify_snapshot normalization.
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
    candidates = _snapshots_with_prefix()
    if not candidates:
        raise _EnableError(
            "timeshift --create succeeded but no snapshot with the ark "
            f"comment ({TIMESHIFT_SNAPSHOT_PREFIX}) is listed — abort "
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
    On failure the freshly created snapshot is deleted best-effort so a
    failed enable never leaves an orphaned (unreferenced) snapshot behind.
    """
    payload = {
        "snapshot_id": snapshot_id,
        "mode": "unrestricted",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = Path(f"{STATE_FILE}.tmp")
    try:
        with tmp.open("w") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chown(tmp, 0, 0)
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        try:
            _timeshift_delete(snapshot_id)
            cleanup = f"snapshot {snapshot_id} deleted — nothing to abort"
        except _EnableError:
            cleanup = (
                f"snapshot {snapshot_id} could not be deleted — "
                "run: ark abort to roll back"
            )
        raise _EnableError(
            f"failed to write {STATE_FILE}: {e}\n  {cleanup}"
        ) from None


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
    except immutable_lib.ImmutableError as e:
        opslog.error(str(e))
        opslog.end_session("enable", "FAILED")
        sys.exit(f"Error: {e}\n"
                 "  Run: ark abort to roll back (gate still armed)")
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
    counts = netmgr.blocklist.manage.blocklist_counts()
    opslog.ok(
        f"blocklist ready: custom {counts['custom']}, "
        f"upstream {counts['upstream']} ({counts['sources']} sources), "
        f"exemptions {counts['exemptions']}, generated {counts['generated']} domains"
    )
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
    print(
        f"    - Blocklist: {counts['custom']} custom, "
        f"{counts['upstream']} upstream ({counts['sources']} sources), "
        f"{counts['exemptions']} exemptions"
    )
    print("    - Reboot\n")
    opslog.warn(
        "After enabling, blocklist sources cannot be modified "
        "until the timelock expires (netmgr sources/blocklist edits "
        "are unrestricted-only)"
    )
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
            f"cask failed ({e})\n  Run: ark abort to roll back "
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
            "  Run: ark abort to roll back (gate still armed)"
        ) from None

    opslog.set_step("Mode write")
    mode.write("focused")
    if mode.read() != "focused":
        raise _EnableError(
            f"failed to verify mode write — check {ARK_DATA_DIR}/mode\n"
            "  Run: ark abort to roll back (gate still armed)"
        )
    opslog.ok("mode = focused")

    opslog.set_step("Sudo removal")
    from ark import deluser_sudo
    if not deluser_sudo():
        raise _EnableError(
            "failed to remove user from sudo group\n"
            "  Run: ark abort to roll back (gate still armed)"
        )
    opslog.ok("user removed from sudo group")

    opslog.end_session("enable", "OK")
    _delete_state()
    reboot_with_countdown(force=True)
