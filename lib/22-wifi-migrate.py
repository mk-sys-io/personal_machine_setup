#!/usr/bin/env python3
"""22-wifi-migrate.py — Migrate Reboot-1 ifupdown WiFi to NetworkManager.

Port of lib/22-wifi-migrate.sh (deleted 2026-10-22). Called from install.py
only. Position: after 20-packages (NM installed), before 05-home-skeleton;
net must be alive before 40-restore.

Flow (unchanged from the bash original):
  detect ifupdown wifi stanza -> extract SSID/PSK -> enable NM ->
  `nmcli con add` profile -> verify managed/connected -> strip wifi lines
  from interfaces (backup kept) -> final connectivity check.
  Unmanaged shortcut: with [ifupdown] managed=false the device stays
  unmanaged while the stanza exists, so verify-before-strip can never pass
  — in that case the old stack is torn down first (ifdown + orphan
  wpa_supplicant/dhcpcd killed), the stanza stripped (backup kept),
  networking/NM restarted, then the same verify + connectivity check.
  Order note: NM is enabled BEFORE `nmcli con add` because nmcli needs the
  daemon to write the profile (deviates from the plan's list order).

Exit contract (0=pass, 1=fail, 2=skip, 3=partial):
  0  migrated now, or already converged (idempotent re-run)
  1  broken (unparsable stanza, unmanaged device, no connectivity) —
     install.py MUST treat this as failure and block the reboot prompt
  2  no wifi stanza ever existed — nothing to migrate, not a failure
  3  partial: the system changed (stanza stripped) but the link did not
     come up. install.py blocks the reboot prompt on FAIL *and* PARTIAL
     for this step, so a partial 22 still vetoes the reboot.
  Signal to the install.py runner on unmanaged: non-zero exit + the
  greppable line: WIFI UNMANAGED.
  Never silently deletes: backup kept, loopback-only strip only after the
  NM profile verifies. interfaces.d/* out of scope (v1).

Language decision (recorded 2026-09-18, ported 2026-10-22): the bash
original stayed bash because "C5 dominates" (thin syscall sequence, no
JSON/API/retry/merge content) and install.py consumes it across a process
boundary (exit code + greppable WIFI UNMANAGED). The port reverses that
call: the bash version carried defect classes a rewrite in the same
language would not have fixed. Each was found by inspection + execution:

  D1  rc=2 was overloaded. `set -euo pipefail` let a tool's exit code
      become the module's: awk exits 2 on a fatal read error, and
      install.py maps rc=2 to SKIP (opslog.map_rc) — so a failed strip
      silently read as "nothing to migrate" AND lifted the reboot veto
      (install.py gates the veto on FAIL). Here every failure raises
      MigrationError and exits 1; a tool's rc never becomes the module's.
  D2  the strip wrote the file it was reading: `sudo awk FILE | tee FILE`
      truncates the target while awk may still be reading it (repro: the
      file came back empty). The write is staged + renamed now, and the
      result is read back and checked against the expected invariants
      before the step reports success. A failed check restores the backup.
  D3  the RESULT trailer was hand-built with printf and escaped only
      backslash and quote; a TAB (0x09) in the SSID made it invalid JSON
      and install.py dropped the message. json.dumps cannot get this wrong.
  D4  nine of ten failure exits printed no trailer, so install.py's
      summary showed FAIL with an empty reason. One except branch now
      emits the trailer on every path.
  D5  `|| true` and `2>/dev/null` hid the real cause (nmcli's own error,
      sudo's own error). Every call keeps stderr and the failure message
      embeds it.
  D6  `nmcli -t` escapes ':' and '\\' (man nmcli: -e/--escape, default
      yes), so a colon-SSID profile looked absent and `con add` failed
      with "already exists" on a healthy machine. Names are read here with
      `nmcli --escape=no -g NAME` and compared exactly.
  D7  rc=3 (PARTIAL) was declared in the contract but never emitted;
      "the stanza is already stripped but the link is down" was
      indistinguishable from "nothing happened".

Not fixed by the port (stated, not hidden): the PSK still travels in
nmcli's argv, so it is visible in `ps` for the life of that process; an
SSID/PSK starting with '-' is still parsed by nmcli as an option.

Verification: --dry-run prints the parse result, the detected device, the
NM state and a redacted diff of the proposed interfaces rewrite. It reads
only — it never writes, never adds a profile, never restarts a service.

Stdlib only. Run standalone: python3 lib/22-wifi-migrate.py [--dry-run]
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from preconditions import require_user  # noqa: E402  (ADR-011 refuse-root guard)

INTERFACES = Path("/etc/network/interfaces")
RESOLV_CONF = Path("/etc/resolv.conf")
REBOOT1_DOC = "docs/recovery/bootstrap-wifi.md"
PING_TARGET = "9.9.9.9"
PING_WAIT_S = 10
WAIT_CONNECTED_S = 30
POLL_INTERVAL_S = 2
PREFIX = "wifi-migrate: "

NM_READ_TIMEOUT = 30
NM_WRITE_TIMEOUT = 120
SYSTEMD_TIMEOUT = 60

STATUS_OK = "OK"
STATUS_SKIP = "SKIP"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAIL = "FAIL"

EXIT_FOR_STATUS = {
    STATUS_OK: 0,
    STATUS_SKIP: 2,
    STATUS_PARTIAL: 3,
    STATUS_FAIL: 1,
}

# bash: grep -m1 -E '^[[:space:]]*wpa-ssid([[:space:]=]+|$)'
_WPA_LINE_RE = re.compile(r"^[^\S\n]*wpa-(?:ssid|psk)(?:[\s=]+|$)")
# bash: sed -E 's/^[[:space:]]*wpa-ssid[[:space:]=]*//'
_KEY_STRIP_RE = re.compile(r"^[^\S\n]*wpa-(?:ssid|psk)[\s=]*")
# bash: awk '/^[[:space:]]/' — a line whose first character is whitespace.
# An empty line has no first character and does NOT match (it ends the
# skipped stanza block, exactly as in awk).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")


class MigrationError(Exception):
    """Fatal migration failure.

    Carries the operator-facing message plus any warnings collected on the
    way to the failure (e.g. "networking restart failed"). main() turns it
    into the single FAIL trailer.
    """

    def __init__(self, message: str, warnings: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.warnings = warnings or []


@dataclass
class Outcome:
    """Machine-readable result of the step (install.py StepResult)."""

    status: str
    changed: bool = False
    message: str = ""
    warnings: list[str] = field(default_factory=list)

    def trailer(self) -> str:
        payload: dict[str, object] = {
            "status": self.status,
            "changed": self.changed,
            "message": sanitize(self.message),
        }
        if self.warnings:
            payload["warnings"] = [sanitize(w) for w in self.warnings]
        return "RESULT " + json.dumps(payload, ensure_ascii=True)


@dataclass
class NmDevice:
    """One row of `nmcli device status`."""

    device: str
    kind: str
    state: str


# ── logging (stdout for milestones, stderr for warn/error) ────────────────


def sanitize(text: str) -> str:
    """Strip control chars, surrogates and newlines.

    Log-injection defense (the bash common.sh did the same) plus encoding
    safety: an SSID is 32 arbitrary bytes, and a lone surrogate (from the
    surrogateescape decode) would blow up stdout encoding.
    """
    return _CONTROL_RE.sub(" ", text)


def log(msg: str) -> None:
    print(f"{PREFIX}{sanitize(msg)}")


def log_ok(msg: str) -> None:
    print(f"{PREFIX}OK   {sanitize(msg)}")


def log_warn(msg: str) -> None:
    print(f"{PREFIX}WARN {sanitize(msg)}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"{PREFIX}ERROR {sanitize(msg)}", file=sys.stderr)


def log_step(msg: str) -> None:
    print(f"{PREFIX}STEP {sanitize(msg)}")


# ── command helpers ───────────────────────────────────────────────────────


def run(
    cmd: list[str],
    *,
    what: str,
    timeout: int = NM_READ_TIMEOUT,
    sudo: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing stdout/stderr.

    stdin is inherited on purpose: the child shares the operator's
    terminal, so `sudo` can prompt (install.py:698-703 — do not set
    stdin=DEVNULL here). errors="surrogateescape" keeps arbitrary SSID
    bytes round-trippable.
    """
    argv = (["sudo"] + cmd) if sudo else cmd
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MigrationError(
            f"{what} timed out after {timeout}s: {' '.join(argv)}"
        ) from exc
    except OSError as exc:
        raise MigrationError(f"{what} could not start: {exc}") from exc


def run_sudo(
    cmd: list[str], *, what: str, timeout: int = NM_READ_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    return run(cmd, what=what, timeout=timeout, sudo=True)


def detail(proc: subprocess.CompletedProcess[str]) -> str:
    """One-line evidence from a failed command (stderr first, then stdout)."""
    text = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    return text.replace("\n", " ")[:400]


# ── pure helpers (ported from the bash original) ──────────────────────────


def strip_value(raw: str) -> str:
    """bash strip_val() (22-wifi-migrate.sh:113-124).

    Split on the FIRST separator run, strip one matched quote pair;
    unquoted values cut at whitespace/`#` (renderer comment semantics).
    """
    value = raw.lstrip()
    quote = value[:1]
    if quote in ('"', "'"):
        value = value[1:]
        index = value.find(quote)
        if index >= 0:
            value = value[:index]
        return value
    for index, char in enumerate(value):
        if char.isspace() or char == "#":
            return value[:index]
    return value


def extract_key(lines: list[str], key: str) -> str | None:
    """bash: grep -m1 -E 'wpa-ssid|wpa-psk' + sed + strip_val.

    Returns the stripped value, or None when the key is absent.
    """
    pattern = re.compile(rf"^[^\S\n]*{re.escape(key)}(?:[\s=]+|$)")
    for line in lines:
        if pattern.match(line):
            return strip_value(_KEY_STRIP_RE.sub("", line, count=1))
    return None


def split_lines(text: str) -> list[str]:
    """Split on newlines only (not on \v, \f, U+2028 — str.splitlines does).

    An empty file yields no lines, so a rewrite of it stays empty (awk
    prints nothing for an empty input — the parity detail matters for
    /etc/network/interfaces).
    """
    if not text:
        return []
    if text.endswith("\n"):
        return text[:-1].split("\n")
    return text.split("\n")


def strip_stanza_lines(lines: list[str], iface: str) -> list[str]:
    """bash: the awk program in strip_stanza (22-wifi-migrate.sh:232-242).

    Rule for rule, in the awk's order:
      1. `iface <iface>` starts a skipped block; the header line is dropped.
      2. while skipped, indented lines are dropped.
      3. any other line ends the skipped block.
      4. `auto`/`allow-hotplug` lines are rebuilt without <iface> (and
         dropped when nothing is left). This normalizes their spacing —
         the awk did the same via `print $1 rest`.
      5. every wpa-ssid/wpa-psk line is dropped.
      6. everything else is preserved verbatim.
    """
    out: list[str] = []
    skipping = False
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "iface" and parts[1] == iface:
            skipping = True
            continue
        if skipping and line[:1].isspace():
            continue
        skipping = False
        if parts and parts[0] in ("allow-hotplug", "auto"):
            rest = [part for part in parts[1:] if part != iface]
            if rest:
                out.append(f"{parts[0]} {' '.join(rest)}")
            continue
        if _WPA_LINE_RE.match(line):
            continue
        out.append(line)
    return out


def iface_names(lines: list[str]) -> set[str]:
    """The set of devices that still have an `iface` stanza."""
    names: set[str] = set()
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "iface":
            names.add(parts[1])
    return names


# ── system access ─────────────────────────────────────────────────────────


def read_file(path: Path, *, sudo: bool = False) -> str:
    """Read a file (root-only files need sudo). Raises MigrationError."""
    proc = (
        run_sudo(["cat", str(path)], what=f"read {path}")
        if sudo
        else run(["cat", str(path)], what=f"read {path}")
    )
    if proc.returncode != 0:
        raise MigrationError(
            f"Cannot read {path} (cat failed, rc={proc.returncode}): {detail(proc)}"
        )
    return proc.stdout


def write_atomic(path: Path, text: str, mode: int, uid: int, gid: int) -> None:
    """Write `text` to `path` without ever truncating the target in place.

    Staged copy + same-directory rename (rename(2) is atomic), so a reader
    sees either the old file or the new one — never a half-written file
    (the bash original's `awk FILE | tee FILE` could empty it, D2).

    A symlinked target is written THROUGH (cp follows the destination
    link) instead of being replaced by the rename.
    """
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", errors="surrogateescape", delete=False
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    try:
        os.chmod(temp_path, 0o600)
        if path.is_symlink():
            proc = run_sudo(
                ["cp", str(temp_path), str(path)], what=f"write {path} (via link)"
            )
            if proc.returncode != 0:
                raise MigrationError(
                    f"Cannot write {path} (rc={proc.returncode}): {detail(proc)}"
                )
            return

        staging = path.with_name(f".{path.name}.new")
        proc = run_sudo(
            ["cp", str(temp_path), str(staging)], what=f"stage {path}"
        )
        if proc.returncode != 0:
            raise MigrationError(
                f"Cannot stage {path} (rc={proc.returncode}): {detail(proc)}"
            )
        try:
            proc = run_sudo(
                ["chmod", f"{mode:04o}", str(staging)], what=f"chmod {staging}"
            )
            if proc.returncode != 0:
                raise MigrationError(
                    f"Cannot set mode on {staging} (rc={proc.returncode}): {detail(proc)}"
                )
            if uid != 0 or gid != 0:
                proc = run_sudo(
                    ["chown", f"{uid}:{gid}", str(staging)], what=f"chown {staging}"
                )
                if proc.returncode != 0:
                    raise MigrationError(
                        f"Cannot set owner on {staging} "
                        f"(rc={proc.returncode}): {detail(proc)}"
                    )
            proc = run_sudo(
                ["mv", "-f", str(staging), str(path)], what=f"replace {path}"
            )
            if proc.returncode != 0:
                raise MigrationError(
                    f"Cannot replace {path} (rc={proc.returncode}): {detail(proc)}"
                )
        except MigrationError:
            run_sudo(["rm", "-f", str(staging)], what=f"clean {staging}")
            raise
    finally:
        temp_path.unlink(missing_ok=True)


def nm_devices() -> list[NmDevice]:
    """`nmcli device status` as structured rows (unescaped)."""
    proc = run(
        ["nmcli", "--escape=no", "-g", "DEVICE,TYPE,STATE", "device", "status"],
        what="nmcli device status",
    )
    if proc.returncode != 0:
        raise MigrationError(
            f"nmcli device status failed (rc={proc.returncode}): {detail(proc)}"
        )
    devices: list[NmDevice] = []
    for line in proc.stdout.split("\n"):
        parts = line.split(":", 2)
        if len(parts) == 3:
            devices.append(NmDevice(parts[0], parts[1], parts[2]))
    return devices


def device_state(iface: str) -> str | None:
    """Current NM state of a wifi device, or None if NM does not see it."""
    for device in nm_devices():
        if device.device == iface and device.kind == "wifi":
            return device.state
    return None


def profile_names() -> list[str]:
    """Connection profile names, unescaped and exact (D6)."""
    proc = run(
        ["nmcli", "--escape=no", "-g", "NAME", "connection", "show"],
        what="nmcli connection show",
    )
    if proc.returncode != 0:
        raise MigrationError(
            f"nmcli connection show failed (rc={proc.returncode}): {detail(proc)}"
        )
    return [line for line in proc.stdout.split("\n") if line != ""]


def profile_exists(ssid: str) -> bool:
    return ssid in profile_names()


def service_enabled(unit: str) -> bool:
    return run(["systemctl", "is-enabled", unit], what=f"is-enabled {unit}").returncode == 0


def wait_connected(iface: str) -> bool:
    """Poll until the device reports `connected` (up to WAIT_CONNECTED_S).

    Association/DHCP timing varies, so a fixed sleep flakes false-negative.
    """
    log(f"Waiting for '{iface}' to associate (up to {WAIT_CONNECTED_S}s)...")
    deadline = time.monotonic() + WAIT_CONNECTED_S
    while True:
        if device_state(iface) == "connected":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_S)


# ── the migration itself ──────────────────────────────────────────────────


def backup_interfaces() -> Path:
    """Copy the interfaces file aside (mode/owner preserved)."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = Path(f"{INTERFACES}.bak-{stamp}")
    proc = run_sudo(["cp", "-a", str(INTERFACES), str(backup)], what="backup interfaces")
    if proc.returncode != 0:
        raise MigrationError(
            f"Cannot back up {INTERFACES} (rc={proc.returncode}): {detail(proc)}"
        )
    log(f"Backup: {backup}")
    return backup


def strip_stanza_file(iface: str, backup: Path) -> None:
    """Strip the wifi stanza, atomically, and verify the result (D2)."""
    original_text = read_file(INTERFACES, sudo=True)
    original_lines = split_lines(original_text)
    kept = strip_stanza_lines(original_lines, iface)
    new_text = "".join(f"{line}\n" for line in kept)

    info = os.stat(INTERFACES)
    mode = stat.S_IMODE(info.st_mode)
    write_atomic(INTERFACES, new_text, mode, info.st_uid, info.st_gid)
    log_ok(f"WiFi stanza stripped from {INTERFACES} (backup {backup}).")

    # Post-condition: the rewrite must have removed the WPA lines and kept
    # every other device's stanza. A mismatch restores the backup.
    verify_lines = split_lines(read_file(INTERFACES, sudo=True))
    survivors = [line for line in verify_lines if _WPA_LINE_RE.match(line)]
    expected = iface_names(original_lines) - {iface}
    missing = expected - iface_names(verify_lines)
    if survivors or missing:
        restore_backup(backup, mode, info.st_uid, info.st_gid)
        raise MigrationError(
            f"Post-strip check failed on {INTERFACES} — backup {backup} restored. "
            f"residual wpa lines: {len(survivors)}, lost stanzas: {sorted(missing)}"
        )


def restore_backup(backup: Path, mode: int, uid: int, gid: int) -> None:
    """Put the backup back (best effort — called on a failed post-check)."""
    log_warn(f"Restoring {INTERFACES} from {backup}")
    try:
        write_atomic(INTERFACES, read_file(backup, sudo=True), mode, uid, gid)
    except MigrationError as exc:
        log_error(f"Restore from {backup} failed: {exc.message}")


def restore_resolv_conf(text: str) -> None:
    """Put back the pre-migration resolv.conf content.

    dhcpcd clears its resolv.conf stanza on ifdown and NM runs dns=none
    (90-dns-none.conf) so it never rewrites one — restore the previous
    content if the file lost every nameserver.
    """
    if not text:
        return
    current = ""
    try:
        current = read_file(RESOLV_CONF)
    except MigrationError:
        current = ""
    # bash: `! grep -q '^nameserver' /etc/resolv.conf` — anchored at the
    # start of a line, and a missing/unreadable file counts as "lost".
    if any(line.startswith("nameserver") for line in split_lines(current)):
        return
    mode, uid, gid = 0o644, 0, 0
    if RESOLV_CONF.exists():
        info = os.stat(RESOLV_CONF)
        mode, uid, gid = stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid
    write_atomic(RESOLV_CONF, text, mode, uid, gid)
    log("Restored resolv.conf (cleared on ifdown; NM dns=none would not rewrite it)")


def teardown_old_stack(iface: str) -> str:
    """Kill the ifupdown stack that still owns the device.

    * ifdown only works while the stanza is still present.
    * the pkills catch hook-started orphans (per-iface wpa_supplicant
      holds the supplicant socket; NM then parks the device at state
      20 unavailable) even after an earlier strip-only partial run.
    * [d] bracket trick: stops pkill -f from regex-matching the
      killer's own sudo/pkill cmdline and taking down the parent.
    NOTE: this bounces the active link — run from the console, not over
    SSH/wifi.
    """
    resolv_before = ""
    try:
        resolv_before = read_file(RESOLV_CONF)
    except MigrationError:
        resolv_before = ""

    proc = run_sudo(["ifdown", iface], what=f"ifdown {iface}")
    if proc.returncode != 0:
        log(f"ifdown {iface} returned rc={proc.returncode} (ignored)")
    proc = run_sudo(
        ["pkill", "-f", f"[w]pa_supplicant.*-i {iface}"], what="pkill wpa_supplicant"
    )
    if proc.returncode != 0:
        log(f"pkill wpa_supplicant for {iface} returned rc={proc.returncode} (ignored)")
    proc = run_sudo(["pkill", "-f", f"[d]hcpcd.*{iface}"], what="pkill dhcpcd")
    if proc.returncode != 0:
        log(f"pkill dhcpcd for {iface} returned rc={proc.returncode} (ignored)")
    time.sleep(1)
    return resolv_before


def preview(
    ssid: str,
    psk: str,
    iface: str,
    state: str | None,
    has_profile: bool,
    lines: list[str],
) -> Outcome:
    """--dry-run: report what the real run would do. Changes nothing."""
    proposed = strip_stanza_lines(lines, iface)
    current_text = "".join(f"{line}\n" for line in lines)
    proposed_text = "".join(f"{line}\n" for line in proposed)
    diff = "\n".join(
        difflib.unified_diff(
            split_lines(current_text),
            split_lines(proposed_text),
            fromfile=str(INTERFACES),
            tofile=f"{INTERFACES} (proposed)",
            lineterm="",
        )
    )
    print(f"{PREFIX}dry-run — no changes made")
    log(f"ssid: {sanitize(ssid)}")
    log(f"psk: present, {len(psk)} chars (value not shown)")
    log(f"device: {iface} (nm state: {state or 'unknown'})")
    log(f"profile '{sanitize(ssid)}': {'exists' if has_profile else 'absent'}")
    log("would: enable NetworkManager, add the NM profile, strip the stanza,")
    log("      verify managed+connected, then check connectivity")
    if diff:
        print(redact(diff, psk))
    else:
        log("proposed interfaces content: identical to the current file")
    return Outcome(STATUS_OK, False, f"dry-run: would migrate {ssid} on {iface}")


def redact(text: str, secret: str) -> str:
    """Blank a secret before it reaches the terminal or the transcript."""
    if secret:
        text = text.replace(secret, "<redacted>")
    return text


def migrate(dry_run: bool) -> Outcome:
    """Run the migration. Raises MigrationError on any failure."""
    if shutil.which("nmcli") is None:
        log_warn("nmcli missing — NetworkManager not converged (stage-0/20 package gap).")
        return Outcome(STATUS_SKIP, False, "nmcli missing - package gap")

    if not INTERFACES.exists():
        log(f"No {INTERFACES} — nothing to migrate.")
        return Outcome(STATUS_SKIP, False, f"no {INTERFACES}")

    text = read_file(INTERFACES, sudo=True)
    lines = split_lines(text)

    ssid = extract_key(lines, "wpa-ssid")
    if ssid is None:
        log(f"No wpa-ssid stanza in {INTERFACES} — nothing to migrate.")
        return Outcome(STATUS_SKIP, False, "no wpa-ssid stanza")
    psk = extract_key(lines, "wpa-psk") or ""

    if not ssid:
        raise MigrationError(
            f"Empty SSID in {INTERFACES} — refusing to migrate. See {REBOOT1_DOC}."
        )
    if not psk:
        raise MigrationError(
            f"SSID '{sanitize(ssid)}' has no wpa-psk (open network?) — refusing to "
            f"migrate. See {REBOOT1_DOC}."
        )

    devices = nm_devices()
    wifi = next((device for device in devices if device.kind == "wifi"), None)
    if wifi is None:
        raise MigrationError(
            "No wifi device visible to NetworkManager. "
            "Check firmware: dmesg | grep -i firmware; rfkill list."
        )
    iface = wifi.device

    has_profile = profile_exists(ssid)
    state = device_state(iface)

    if has_profile and state == "connected":
        log_ok(f"WiFi already migrated ('{sanitize(ssid)}' connected on {iface}).")
        return Outcome(STATUS_OK, False, f"already migrated: {sanitize(ssid)}")

    if dry_run:
        return preview(ssid, psk, iface, state, has_profile, lines)

    if has_profile:
        log(f"Profile '{sanitize(ssid)}' exists but not connected — activating.")
        proc = run_sudo(
            ["nmcli", "connection", "up", ssid],
            what="nmcli connection up",
            timeout=NM_WRITE_TIMEOUT,
        )
        if proc.returncode == 0:
            log_ok(f"WiFi profile '{sanitize(ssid)}' activated.")
            return Outcome(STATUS_OK, True, f"activated {sanitize(ssid)}")
        log_warn(f"Activation failed (rc={proc.returncode}): {detail(proc)}")
        log_warn(f"Re-adding profile '{sanitize(ssid)}'.")
        run_sudo(
            ["nmcli", "connection", "delete", ssid], what="nmcli connection delete"
        )

    log_step(f"Migrating WiFi '{sanitize(ssid)}' to NetworkManager")
    if service_enabled("NetworkManager"):
        log_ok("NetworkManager already enabled")
    else:
        proc = run_sudo(
            ["systemctl", "enable", "--now", "NetworkManager"],
            what="enable NetworkManager",
            timeout=SYSTEMD_TIMEOUT,
        )
        if proc.returncode != 0:
            raise MigrationError(
                f"NetworkManager enable failed (rc={proc.returncode}): {detail(proc)}"
            )
        log_ok("NetworkManager enabled")

    proc = run_sudo(
        ["systemctl", "start", "NetworkManager"],
        what="start NetworkManager",
        timeout=SYSTEMD_TIMEOUT,
    )
    if proc.returncode != 0:
        log_warn("NetworkManager start failed — connection add below is the verifier")

    proc = run_sudo(
        [
            "nmcli",
            "connection",
            "add",
            "type",
            "wifi",
            "con-name",
            ssid,
            "ifname",
            iface,
            "ssid",
            ssid,
            "wifi-sec.key-mgmt",
            "wpa-psk",
            "wifi-sec.psk",
            psk,
        ],
        what="nmcli connection add",
        timeout=NM_WRITE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise MigrationError(
            f"nmcli connection add failed for '{sanitize(ssid)}' "
            f"(rc={proc.returncode}): {detail(proc)}"
        )
    log_ok(f"NM profile '{sanitize(ssid)}' written")

    warnings: list[str] = []
    stripped = False
    backup: Path | None = None
    resolv_before = ""

    # The state is re-read here (not the one captured before the profile
    # add): the bash asked nmcli again at this point, and NM may already
    # have claimed the device.
    state = device_state(iface)
    if state in ("unmanaged", "unavailable"):
        # Chicken-and-egg (live REDMI-15C case): stock NM.conf has
        # [ifupdown] managed=false, so the plugin keeps the device
        # unmanaged while the stanza exists — verify-before-strip can
        # never pass and the strip would never run. Tear the old stack
        # down FIRST, then strip (backup kept), then let NM claim a free
        # device.
        log(
            f"{iface} unmanaged/unavailable (ifupdown leftovers) — "
            "tearing down before verify."
        )
        resolv_before = teardown_old_stack(iface)
        backup = backup_interfaces()
        strip_stanza_file(iface, backup)
        stripped = True

        proc = run_sudo(
            ["systemctl", "restart", "networking"],
            what="restart networking",
            timeout=SYSTEMD_TIMEOUT,
        )
        if proc.returncode != 0:
            warnings.append("networking restart failed")
            log_warn("networking restart failed")
        proc = run_sudo(
            ["nmcli", "device", "set", iface, "managed", "yes"],
            what="nmcli device set managed",
        )
        if proc.returncode != 0:
            warnings.append("nmcli device set managed failed")
            log_warn("nmcli device set managed failed")
        proc = run_sudo(
            ["systemctl", "restart", "NetworkManager"],
            what="restart NetworkManager",
            timeout=SYSTEMD_TIMEOUT,
        )
        if proc.returncode != 0:
            warnings.append("NetworkManager restart failed")
            log_warn("NetworkManager restart failed")
        time.sleep(2)

    wait_connected(iface)

    if resolv_before:
        restore_resolv_conf(resolv_before)

    state = device_state(iface)
    stripped_note = f"stanza stripped (backup {backup})" if backup else "stanza stripped"
    if state == "unmanaged":
        log_error(
            f"WIFI UNMANAGED: {iface} is unmanaged — ifupdown still owns it. "
            "Reboot blocked."
        )
        if stripped:
            return Outcome(
                STATUS_PARTIAL,
                True,
                f"{stripped_note} but {iface} stayed unmanaged",
                warnings,
            )
        raise MigrationError(
            f"WIFI UNMANAGED: {iface} is unmanaged — ifupdown still owns it. "
            "Reboot blocked.",
            warnings,
        )
    if state != "connected":
        log_error(
            f"WIFI UNMANAGED: {iface} not connected after migration. Reboot blocked."
        )
        if stripped:
            return Outcome(
                STATUS_PARTIAL,
                True,
                f"{stripped_note} but {iface} did not connect",
                warnings,
            )
        raise MigrationError(
            f"WIFI UNMANAGED: {iface} not connected after migration. Reboot blocked.",
            warnings,
        )
    log_ok(f"WiFi managed and connected ({iface}).")

    if not stripped:
        backup = backup_interfaces()
        strip_stanza_file(iface, backup)
        stripped = True

    proc = run(["ping", "-c1", f"-W{PING_WAIT_S}", PING_TARGET], what="ping")
    if proc.returncode == 0:
        log_ok("Connectivity alive after migration.")
        return Outcome(
            STATUS_OK, True, f"migrated {sanitize(ssid)} to NetworkManager", warnings
        )
    raise MigrationError(
        f"No connectivity after migration ({stripped_note}). Reboot blocked.", warnings
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate the ifupdown WiFi stanza to NetworkManager."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what the migration would do; read-only, no changes",
    )
    args = parser.parse_args(argv[1:])

    if not require_user("lib/22-wifi-migrate.py", "python3 lib/22-wifi-migrate.py"):
        return 1

    try:
        outcome = migrate(dry_run=args.dry_run)
    except MigrationError as exc:
        log_error(exc.message)
        outcome = Outcome(STATUS_FAIL, False, exc.message, exc.warnings)
    except (OSError, subprocess.SubprocessError) as exc:
        log_error(f"unexpected error: {exc}")
        outcome = Outcome(STATUS_FAIL, False, f"unexpected error: {exc}")

    print(outcome.trailer())
    return EXIT_FOR_STATUS[outcome.status]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
