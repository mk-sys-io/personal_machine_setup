#!/usr/bin/env python3
"""Abort — roll back an incomplete `ark enable` via snapshot restore.

Standalone CLI + library for the `ark abort` subcommand. Restores the
exact pre-enable timeshift snapshot recorded in `{{ .Env.ARK_DATA_PATH }}/state/enable.json`
— the sole abort oracle (no log reads). Its presence means the lockdown did
not cleanly finish; missing → "Nothing to abort", exit 0.

Fail-closed: every gate (timeshift excludes, restore hook, ark-exclusivity,
snapshot presence) must pass before the restore is attempted, else abort
refuses with a recovery hint. A confirmation prompt is shown before proceeding.

An online restore never returns control: timeshift appends ``reboot -f`` and
the process dies; ``END abort: OK`` is written by the deployed restore-hook
(``/etc/timeshift/restore-hooks.d/99-ark-abort-log``), never by this module.
Control returning from the restore call is therefore a failure path.

Grounded in ark-abort-flow.md (plans/ark-rework/ark-abort-flow.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import immutable_lib
import opslog

# ── Paths (gomplate-rendered at deploy; repo source keeps the markers) ───────

ARK_DATA_DIR: str = "{{ .Env.ARK_DATA_PATH }}"
STATE_FILE: str = f"{ARK_DATA_DIR}/state/enable.json"
ARK_LOG: str = f"{ARK_DATA_DIR}/logs/abort.log"
MODE_FILE: str = f"{ARK_DATA_DIR}/mode"
CASK_DIR: str = f"{ARK_DATA_DIR}/cask"
RESOLV_CONF: str = "/etc/resolv.conf"
TIMESHIFT_JSON: str = "/etc/timeshift/timeshift.json"
RESTORE_HOOK: str = "/etc/timeshift/restore-hooks.d/99-ark-abort-log"
TIMESHIFT_BIN: str = "timeshift"

# ── Constants ─────────────────────────────────────────────────────────────────

_TS_EXCLUDES: tuple[str, str] = (
    f"{ARK_DATA_DIR}/logs/***",
    f"{ARK_DATA_DIR}/state/***",
)
# Restore-prep clear set. metadata.json is a no-op once the Phase 6 tier-down
# lands (already-clear files clear as no-ops).
_CLEAR_PATHS: tuple[str, ...] = (
    MODE_FILE,
    f"{CASK_DIR}/system.cask",
    f"{CASK_DIR}/mobile.cask",
    f"{CASK_DIR}/metadata.json",
)
_SCHEDULE_KEYS: tuple[str, ...] = (
    "schedule_monthly",
    "schedule_weekly",
    "schedule_daily",
    "schedule_hourly",
    "schedule_boot",
)
# Matches timeshift snapshot names in both display and on-disk forms:
#   2024-06-07 14:28:16   (display, date_format)
#   2024-06-07_14-28-16   (on-disk directory name)
_SNAPSHOT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2})[-:](\d{2})[-:](\d{2})")
# A timeshift --list snapshot row begins with an index number (optionally a
# `>` marker for the marked snapshot) before the name. Anchoring on the row
# start keeps header/summary lines with embedded dates out of the parse.
_ROW_RE = re.compile(r"^\s*\d+\s+(?:>\s+)?(\d{4}-\d{2}-\d{2})[ _]\d{2}[-:]\d{2}[-:]\d{2}")
# Generous bound for timeshift --restore (a full-system rsync). A hung
# restore must not block `ark abort` forever.
_RESTORE_TIMEOUT = 3600


class EnableState(TypedDict):
    snapshot_id: str
    mode: str
    started_at: str


class AbortError(RuntimeError):
    """Fatal abort-flow failure with a user-facing recovery hint."""


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


# ── Subprocess helper ─────────────────────────────────────────────────────────

def _run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a timeshift call with LC_ALL=C for locale-stable parsing."""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as e:
        raise AbortError(
            f"{cmd[0]} timed out after {timeout}s — a hung call would block "
            "the abort flow indefinitely; inspect the system manually"
        ) from e
    except OSError as e:
        raise AbortError(f"cannot run {cmd[0]}: {e}") from e


# ── State-file gate (the sole abort oracle) ───────────────────────────────────

def read_state() -> EnableState | None:
    """Read enable.json. None when missing; AbortError when unparseable."""
    path = Path(STATE_FILE)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise AbortError(f"state file corrupt: {STATE_FILE} ({e})") from e
    if not isinstance(data, dict):
        raise AbortError(f"state file malformed: {STATE_FILE} — expected an object")
    snapshot_id = data.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise AbortError(
            f"state file has no snapshot_id: {STATE_FILE} — abort refused "
            "(no pinned rollback target)"
        )
    return EnableState(
        snapshot_id=snapshot_id,
        mode=str(data.get("mode", "")),
        started_at=str(data.get("started_at", "")),
    )


# ── timeshift --list parsing ──────────────────────────────────────────────────

def _canonical(name: str) -> str:
    """Normalize a snapshot name to `YYYY-MM-DD HH:MM:SS` (locale-stable)."""
    m = _SNAPSHOT_RE.search(name)
    if not m:
        return name.strip()
    return f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}"


def parse_list(output: str) -> set[str]:
    """Extract the set of snapshot names from `timeshift --list` output.

    Only anchored snapshot rows (leading index number) are considered —
    header/summary lines with embedded dates are ignored.
    """
    names: set[str] = set()
    for line in output.splitlines():
        if not _ROW_RE.match(line):
            continue
        m = _SNAPSHOT_RE.search(line)
        if m:
            names.add(_canonical(m.group(0)))
    return names


def _backup_root(list_output: str) -> Path | None:
    """Backup path from the --list header (`Path : ...`), else common roots."""
    for line in list_output.splitlines():
        if line.lower().startswith("path"):
            _, _, value = line.partition(":")
            if value.strip():
                return Path(value.strip())
    for candidate in ("/run/timeshift/backup", "/timeshift", "/run/timeshift"):
        if Path(candidate).is_dir():
            return Path(candidate)
    return None


def verify_snapshot(snapshot_id: str) -> None:
    """Pin the recorded snapshot in `timeshift --list` — never 'latest'."""
    result = _run([TIMESHIFT_BIN, "--list"])
    if result.returncode != 0:
        raise AbortError(
            f"timeshift --list failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    listed = parse_list(result.stdout)
    target = _canonical(snapshot_id)
    if target in listed:
        opslog.ok(f"Snapshot {snapshot_id} present in timeshift list")
        return

    opslog.warn(f"Snapshot {snapshot_id} not listed by timeshift — raw list follows")
    for line in result.stdout.splitlines():
        opslog.raw(line)

    backup_root = _backup_root(result.stdout)
    disk_name = snapshot_id.replace(" ", "_").replace(":", "-")
    if backup_root is None or not (backup_root / disk_name).is_dir():
        raise AbortError(
            f"recorded snapshot {snapshot_id} was already deleted — abort "
            "refused (no rollback target)"
        )
    raise AbortError(
        f"snapshot {snapshot_id} found on disk but not in `timeshift --list` "
        "— abort refused; inspect timeshift manually"
    )


# ── Fail-closed gates ─────────────────────────────────────────────────────────

def timeshift_exclusive() -> None:
    """timeshift must be ark-exclusive: no schedules, no cron/boot scripts.

    A re-enabled schedule makes the "latest snapshot" ambiguous and could
    silently overwrite the pinned abort target. Shared by enable preflight.
    """
    try:
        cfg = json.loads(Path(TIMESHIFT_JSON).read_text())
    except (OSError, ValueError) as e:
        raise AbortError(f"cannot read {TIMESHIFT_JSON}: {e}") from e
    if not isinstance(cfg, dict):
        raise AbortError(f"malformed {TIMESHIFT_JSON} — expected an object")
    for key in _SCHEDULE_KEYS:
        value = cfg.get(key, False)
        if value not in (False, "false"):
            raise AbortError(
                f"timeshift is not ark-exclusive: {key}={value!r} — "
                "disable all schedules"
            )
    for unit in Path("/etc/systemd/system").glob("timeshift*.timer"):
        raise AbortError(f"timeshift automation present: {unit} — remove it")
    for unit in Path("/etc/systemd/system").glob("timeshift*.service"):
        raise AbortError(f"timeshift automation present: {unit} — remove it")
    for cron in Path("/etc/cron.d").glob("timeshift*"):
        raise AbortError(f"timeshift cron present: {cron} — remove it")


def validate_restore_hook() -> None:
    """Restore hook must be present, executable, and match the spec content."""
    hook = Path(RESTORE_HOOK)
    if not hook.is_file():
        raise AbortError(
            f"restore hook missing: {RESTORE_HOOK} — run: sudo install.sh"
        )
    if not os.access(hook, os.X_OK):
        raise AbortError(f"restore hook not executable: {RESTORE_HOOK}")
    try:
        content = hook.read_text(errors="replace")
    except OSError as e:
        raise AbortError(f"cannot read restore hook: {e}") from e
    for needle in ("ENABLE_STATE={{ .Env.ARK_DATA_PATH }}/state/enable.json",
                   "=== END abort: OK ===",
                   "chattr +i"):
        if needle not in content:
            raise AbortError(
                f"restore hook content mismatch (missing {needle!r}) — "
                "re-run: sudo install.sh"
            )


def check_gates() -> None:
    """Fail-closed gate bundle. Any failure aborts before any mutation."""
    try:
        cfg = json.loads(Path(TIMESHIFT_JSON).read_text())
    except (OSError, ValueError) as e:
        raise AbortError(f"cannot read {TIMESHIFT_JSON}: {e}") from e
    if not isinstance(cfg, dict):
        raise AbortError(f"malformed {TIMESHIFT_JSON} — expected an object")
    excludes = set(cfg.get("exclude") or [])
    for want in _TS_EXCLUDES:
        if want not in excludes:
            raise AbortError(
                f"timeshift exclude entry missing: {want} — run: sudo install.sh"
            )
    validate_restore_hook()
    timeshift_exclusive()


# ── Restore prep (no state mutation) ──────────────────────────────────────────

def _scan_immutable(root: str) -> list[str]:
    """Paths under root with the immutable flag (recursive lsattr).

    Fail-closed: a non-zero lsattr exit is an error, never "no immutables" —
    an unexpected +i file under scope must surface as a pre-restore WARN,
    not be silently ignored.
    """
    result = subprocess.run(
        ["lsattr", "-R", root], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise AbortError(
            f"lsattr -R {root} failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()} — cannot "
            "verify immutables before restore"
        )
    found: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        flags, path = parts
        if len(flags) > 4 and flags[4] == "i":
            found.append(path)
    return found


def restore_prep() -> None:
    """Clear +i before restore; never write mode (a failed restore must not
    claim unrestricted). WARN on any unexpected +i file in scope."""
    for path in _CLEAR_PATHS:
        if os.path.lexists(path):
            immutable_lib.clear_immutable(path)
    if os.path.isfile(RESOLV_CONF) and immutable_lib.is_immutable(RESOLV_CONF):
        immutable_lib.clear_immutable(RESOLV_CONF)
    for path in _scan_immutable(ARK_DATA_DIR):
        if path not in _CLEAR_PATHS:
            opslog.warn(
                f"unexpected immutable file in scope: {path} — restore may fail"
            )


# ── Restore ───────────────────────────────────────────────────────────────────

def restore(snapshot_id: str) -> None:
    """Non-interactive online restore. Never returns on success (timeshift
    forces a reboot); any return is a failure path — no log after the call."""
    result = _run(
        [TIMESHIFT_BIN, "--restore", "--snapshot", snapshot_id, "--scripted", "--yes"],
        timeout=_RESTORE_TIMEOUT,
    )
    detail = result.stderr.strip() or result.stdout.strip() or "no output"
    raise AbortError(
        f"timeshift --restore returned without rebooting (rc={result.returncode}): "
        f"{detail}"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    """Execute the abort flow. 0 = clean exit, 1 = failure (logged FAILED)."""
    opslog.configure("ark", file=ARK_LOG, mode="w", base_dir=ARK_DATA_DIR)
    opslog.session("abort")
    try:
        opslog.set_step("Gate")
        state = read_state()
        if state is None:
            print("Nothing to abort")
            opslog.ok("Nothing to abort — no state file")
            opslog.end_session("abort", "OK")
            return 0

        opslog.ok(f"Armed — enable.json present (snapshot {state['snapshot_id']})")
        check_gates()

        opslog.set_step("Snapshot contract")
        verify_snapshot(state["snapshot_id"])

        opslog.set_step("Restore prep")
        restore_prep()

        print(f"Abort will restore snapshot: {state['snapshot_id']}")
        print("This will roll back root password, sudo, configs, and mode.")
        if not _confirm("Proceed? [y/N] ", default=False):
            print("Cancelled.")
            opslog.end_session("abort", "FAILED")
            return 1

        opslog.set_step("Restore")
        restore(state["snapshot_id"])
    except AbortError as e:
        opslog.error(str(e))
        opslog.end_session("abort", "FAILED")
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except immutable_lib.ImmutableError as e:
        opslog.error(f"restore prep failed: {e}")
        opslog.end_session("abort", "FAILED")
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        opslog.end_session("abort", "FAILED")
        print("\nCancelled.", file=sys.stderr)
        return 1
    return 1  # unreachable — restore() raises on any return


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        print("Error: abort requires root\n  Run: sudo ark abort",
              file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        prog="abort",
        description="Roll back an incomplete `ark enable` (snapshot restore).",
    )

    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
