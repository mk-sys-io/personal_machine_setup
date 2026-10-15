#!/usr/bin/env python3
"""verify — post-install health verifier for linux_setup.

Read-only acceptance check to run after reboot, before 40-restore
(pre-restore gate; re-runnable): proves the state pre-reboot modules cannot
(nvidia.ko binds at boot, ifupdown->NetworkManager migration persists, ark
runtime vs files). Answers "is this system verified stable?" with one screen
of sentences and a single exit code. Never converges, never trunk-wired.

Usage:
    verify [--only a,b] [--json] [--quiet] [--verbose] [--plain]

Exit codes:
    0  all checks OK, SKIP or WARN
    1  at least one FAIL
    2  usage error (bad flag / unknown --only key / run as root)

Stdlib only. Deployed to ~/.local/bin/verify via `make tools`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Output grammar (ported from lib/helpers/common.sh:209-291 — no lib import)
# ---------------------------------------------------------------------------

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

RESET = "\033[0m"
COLORS = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[36m"}

_COLOR = False


@dataclass
class Result:
    """One check outcome: status word + human sentence + optional detail."""

    status: str
    message: str
    detail: str = ""


def sanitize(text: str) -> str:
    """Strip control chars and collapse whitespace so detail stays on one line."""
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def format_line(res: Result) -> str:
    if _COLOR:
        color = COLORS.get(res.status, "")
        head = f"  {color}{res.status:<5}{RESET}"
    else:
        head = f"  {res.status:<5}"
    line = f"{head}{res.message}"
    if res.detail:
        line += f"\n        {res.detail}"
    return line


# ---------------------------------------------------------------------------
# Probe plumbing
# ---------------------------------------------------------------------------


def run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run a read-only command, capturing output. Never raises on rc != 0."""
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def is_vm() -> bool:
    """True inside a VM (systemd-detect-virt -q); False if unavailable."""
    if not shutil.which("systemd-detect-virt"):
        return False
    try:
        return run(["systemd-detect-virt", "-q"], timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

UDEV_UINPUT = Path("/etc/udev/rules.d/10-uinput.rules")
UDEV_NOCLAMP = Path("/etc/udev/rules.d/91-backlight-noclamp.rules")
MODPARAMS = (
    (Path("/sys/module/iwlwifi/parameters/power_save"), "N"),
    (Path("/sys/module/btusb/parameters/enable_autosuspend"), "N"),
)


def check_latch() -> Result:
    """No pending reboot — every other check is void until reboot clears it."""
    home = Path.home()
    markers = [
        home / ".config" / "install" / ".install-need-reboot",
        Path("/run/reboot-required"),
    ]
    pending = [m for m in markers if m.is_file()]
    if not pending:
        return Result(OK, "Latch: no reboot pending")
    names = " + ".join(m.name for m in pending)
    return Result(FAIL, f"Latch: reboot pending ({names}) — fix: reboot")


def check_net() -> Result:
    """DNS + TCP 443 reachability (post-reboot; ark DNS/nft can break it)."""
    if shutil.which("getent") and run(["getent", "hosts", "deb.debian.org"], timeout=10).returncode != 0:
        return Result(FAIL, "Net: DNS resolution failed — fix: check network / ark DNS")
    try:
        with socket.create_connection(("deb.debian.org", 443), timeout=5):
            pass
    except OSError:
        return Result(FAIL, "Net: TCP 443 to deb.debian.org failed — fix: check connectivity")
    return Result(OK, "Net: DNS + TCP443 reachable")


def check_wifi() -> Result:
    """22 migration persisted: a wifi device is connected under NetworkManager."""
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return Result(SKIP, "Wifi: nmcli absent — cannot probe")
    devices = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"], timeout=10)
    wifi = [ln.split(":") for ln in devices.stdout.splitlines() if ln.count(":") >= 2 and ln.split(":")[1] == "wifi"]
    if not wifi:
        return Result(SKIP, "Wifi: no wireless device present")
    if any(parts[2] == "connected" for parts in wifi):
        return Result(OK, "Wifi: connected under NetworkManager")
    states = ", ".join(sorted({parts[2] for parts in wifi}))
    return Result(FAIL, f"Wifi: not connected under NM (state: {states}) — fix: nmcli connection up")


def check_nvidia() -> Result:
    """Driver bound at boot (the check no pre-reboot log can satisfy)."""
    if is_vm():
        return Result(SKIP, "Nvidia: in VM — not applicable")
    lspci = shutil.which("lspci")
    if lspci:
        try:
            out = run(["lspci", "-nn"], timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out and "10de" not in out:
            return Result(SKIP, "Nvidia: no NVIDIA GPU present")
    smi = shutil.which("nvidia-smi")
    if not smi:
        return Result(FAIL, "Nvidia: nvidia-smi missing — fix: reboot, then ./install.py")
    if run(["nvidia-smi", "-q"], timeout=10).returncode != 0:
        return Result(FAIL, "Nvidia: nvidia-smi failed (driver not bound) — fix: reboot")
    lsmod = shutil.which("lsmod")
    if lsmod and "nvidia" not in run(["lsmod"], timeout=10).stdout:
        return Result(FAIL, "Nvidia: nvidia module not loaded — fix: reboot")
    return Result(OK, "Nvidia: smi + module bound")


def check_ark() -> Result:
    """Ark runtime truth via its own CLI (files correct != runtime correct).

    `ark status` requires root. The NOPASSWD grant is command-scoped, so
    probe `ark status` itself via sudo -n, never `sudo -n true`. No
    passwordless sudo -> WARN (a strong check could not run), not SKIP.
    """
    ark = shutil.which("ark")
    if not ark:
        return Result(SKIP, "Ark: ark CLI absent")
    sudo = shutil.which("sudo")
    if not sudo:
        return Result(WARN, "Ark: cannot run strong check (no sudo) — run: sudo ark status")
    try:
        probe = run([sudo, "-n", ark, "status", "--json"], timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return Result(FAIL, "Ark: status probe error — fix: sudo ark status", sanitize(str(exc)))
    if not probe.stdout.strip():
        return Result(WARN, "Ark: could not run strong check (passwordless sudo unavailable) — run: sudo ark status", sanitize(probe.stderr))
    try:
        data = json.loads(probe.stdout)
    except json.JSONDecodeError:
        return Result(FAIL, "Ark: status --json unparseable — fix: sudo ark status", sanitize(probe.stdout[:200]))
    summary = data.get("summary", {})
    fails = summary.get("fail", 0)
    if fails or data.get("ok") is False:
        return Result(FAIL, f"Ark: status reports {fails} fail — fix: sudo ark status")
    return Result(OK, "Ark: status ok")


def check_deploys() -> Result:
    """make dotfiles/dev/tools outputs (the deploy loops skip silently)."""
    home = Path.home()
    config = home / ".config" / "linux_setup" / "config.txt"
    missing = []
    if not (home / ".config" / "sway" / "config").is_file():
        missing.append("~/.config/sway/config (make dotfiles)")
    if not (home / ".local" / "bin" / "init").is_file():
        missing.append("~/.local/bin/init (make tools)")
    if not shutil.which("pi-setup"):
        missing.append("pi-setup on PATH (make tools)")
    if not config.is_file():
        missing.append("~/.config/linux_setup/config.txt (make dotfiles)")
    elif "REPO_ROOT=" not in config.read_text(errors="replace"):
        missing.append("config.txt REPO_ROOT= (make dotfiles)")
    if missing:
        return Result(FAIL, "Deploys: missing " + " ; ".join(missing))
    return Result(OK, "Deploys: sway/config, init, pi-setup, config.txt all present")


def check_units() -> Result:
    """Failed system/user units after boot (catches daemons that died at boot)."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return Result(SKIP, "Units: systemctl absent — cannot probe")
    failed: list[str] = []
    for scope, label in (([], ""), (["--user"], " (user)")):
        probe = run([systemctl, *scope, "--failed", "--no-legend", "--plain"], timeout=10)
        if probe.returncode != 0 and not probe.stdout.strip():
            continue
        for line in probe.stdout.splitlines():
            parts = line.split()
            if parts:
                failed.append(f"{parts[0]}{label}")
    if failed:
        return Result(FAIL, f"Units: {len(failed)} failed — " + ", ".join(failed))
    return Result(OK, "Units: no failed systemd units")


def check_security() -> Result:
    """auditd + fail2ban active post-boot; audit rules loaded (post-boot only)."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return Result(SKIP, "Security: systemctl absent — cannot probe")
    inactive = [u for u in ("auditd", "fail2ban") if run([systemctl, "is-active", u], timeout=10).stdout.strip() != "active"]
    if inactive:
        return Result(FAIL, "Security: inactive " + ", ".join(inactive) + " — fix: sudo systemctl start " + " ".join(inactive))
    auditctl = shutil.which("auditctl")
    sudo = shutil.which("sudo")
    if auditctl and sudo:
        probe = run([sudo, "-n", auditctl, "-l"], timeout=10)
        if probe.returncode != 0:
            return Result(WARN, "Security: auditd+fail2ban active; audit rules unverified (need passwordless sudo)", "run: sudo auditctl -l")
    return Result(OK, "Security: auditd + fail2ban active")


def check_searxng() -> Result:
    """User unit active after boot (enable != active; default.target auto-start)."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return Result(SKIP, "Searxng: systemctl absent — cannot probe")
    listed = run([systemctl, "--user", "list-unit-files", "searxng.service"], timeout=10)
    if listed.returncode != 0 or "searxng.service" not in listed.stdout:
        return Result(SKIP, "Searxng: user unit not installed")
    state = run([systemctl, "--user", "is-active", "searxng"], timeout=10).stdout.strip()
    if state != "active":
        return Result(FAIL, f"Searxng: user unit {state or 'unknown'} — fix: systemctl --user start searxng")
    return Result(OK, "Searxng: user unit active")


def check_hardware() -> Result:
    """30-hardware post-boot state: udev rules present, module params applied."""
    if is_vm():
        return Result(SKIP, "Hardware: in VM — not applicable")
    missing = [p.name for p in (UDEV_UINPUT, UDEV_NOCLAMP) if not p.is_file()]
    if missing:
        return Result(FAIL, "Hardware: udev rule(s) missing: " + ", ".join(missing) + " — fix: ./install.py (30-hardware)")
    bad = []
    for path, want in MODPARAMS:
        if not path.is_file():
            continue
        try:
            val = path.read_text(errors="replace").strip()
        except OSError:
            continue
        if val != want:
            bad.append(f"{path.parent.parent.name}.{path.name}={val}")
    if bad:
        return Result(FAIL, "Hardware: module param(s) not applied: " + ", ".join(bad) + " — fix: reboot")
    return Result(OK, "Hardware: udev rules + module params applied")


# Selection criterion (UCC): a check is warranted only if its failure
# state is NOT already caught by the chain (bootstrap.sh -> install.py ->
# reboot -> 40-restore.py) AND it observes post-reboot-only state (module
# bind, unit active, runtime != files, migration persistence). Even then,
# drop it if its cost exceeds its signal (interactivity, timeout,
# false-FAIL).
CHECKS = [
    ("latch", check_latch),
    ("net", check_net),
    ("wifi", check_wifi),
    ("nvidia", check_nvidia),
    ("ark", check_ark),
    ("deploys", check_deploys),
    ("units", check_units),
    ("security", check_security),
    ("searxng", check_searxng),
    ("hardware", check_hardware),
]
CHECK_KEYS = [name for name, _ in CHECKS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify",
        description="Post-install health verifier (read-only).",
    )
    parser.add_argument("--only", metavar="a,b", help="run only these checks")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="no output; exit code only")
    parser.add_argument("--verbose", action="store_true", help="show probe detail and scope")
    parser.add_argument("--plain", action="store_true", help="disable color")
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    global _COLOR
    args = parse_args(argv)

    if os.geteuid() == 0:
        print(
            "  ERROR: do not run verify as root (ADR-011) — it inspects the "
            "user environment. Run as your normal user.",
            file=sys.stderr,
        )
        return 2

    selected = CHECK_KEYS
    if args.only:
        wanted = [k.strip() for k in args.only.split(",") if k.strip()]
        unknown = [k for k in wanted if k not in CHECK_KEYS]
        if unknown:
            print(f"error: unknown check(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"  valid: {', '.join(CHECK_KEYS)}", file=sys.stderr)
            return 2
        selected = wanted

    _COLOR = (
        sys.stdout.isatty()
        and not args.plain
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM") != "dumb"
    )

    results: list[tuple[str, Result]] = []
    for name, fn in CHECKS:
        if name not in selected:
            continue
        try:
            res = fn()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            res = Result(FAIL, f"{name.capitalize()}: probe error — {sanitize(str(exc))}")
        results.append((name, res))

    passed = sum(1 for _, r in results if r.status == OK)
    warned = sum(1 for _, r in results if r.status == WARN)
    failed = sum(1 for _, r in results if r.status == FAIL)
    skipped = sum(1 for _, r in results if r.status == SKIP)

    if args.json:
        payload = {
            "checks": [
                {
                    "name": name,
                    "status": r.status,
                    "message": r.message,
                    **({"detail": r.detail} if r.detail else {}),
                }
                for name, r in results
            ],
            "summary": {"pass": passed, "fail": failed, "skip": skipped, "warn": warned},
            "ok": failed == 0,
        }
        print(json.dumps(payload, indent=2))
    elif not args.quiet:
        if args.verbose:
            print(f"verify: {len(selected)} check(s): {', '.join(selected)}")
        for _, res in results:
            print(format_line(res))
        print(f"Summary: {passed} passed, {failed} failed, {skipped} skipped, {warned} warned")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
