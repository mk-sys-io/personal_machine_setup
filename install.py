#!/usr/bin/env python3
"""install.py — trunk orchestrator (Phase 20-B: logging reference).

Replaces install.sh:150-179 dispatch with:
  ensure_stage0() -> load_env() -> step table -> reboot prompt.

20-B: per-run transcript (install.<ts>-<label>.log + atomic latest +
prune-100), configure(ownership=user) with post-sudo
re-assert, Popen-tee via opslog.run(), module_session/end_module
markers, slowest-table summary. Self-containment and config retirement
are 20-C.

Contract (a)-(d) lives here (20.4):
  (a) 22 signals via non-zero exit + greppable log_error line.
  (b) idempotency 0/2/1.
  (c) PARTIAL(3) amber only.
  (d) RESULT {json} trailer -> StepResult.

Stdlib only. Run from repo root: ./install.py
"""

from __future__ import annotations

import argparse
import atexit
import datetime
import enum
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

import opslog  # noqa: E402  (anchored on __file__, sudo-safe, CWD-independent)
from preconditions import require_user  # noqa: E402  (refuse-root guard, ADR-011)

PYTHON_FLOOR = (3, 11)
STAGE0_PACKAGES = [
    "git",
    "make",
    "python3",
    "curl",
    "ca-certificates",
    "gnupg",
    "wpasupplicant",
    "wireless-tools",
    "iw",
    "rfkill",
    "xz-utils",
]
PRESENCE_BINARIES = ["git", "make", "python3", "curl", "gpg"]

CONFIG_NAME = "config.txt"


def repo_root() -> Path:
    return REPO_ROOT


def log_dir() -> Path:
    return Path.home() / ".config" / "install"


def needs_reboot_file() -> Path:
    return log_dir() / ".install-need-reboot"


def transcript_path(label: str = "full") -> Path:
    """Per-run transcript path. 20-C wires the standalone label arg."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    base = log_dir() / f"install.{ts}-{label}.log"
    if base.exists():
        return log_dir() / f"install.{ts}-{label}-{os.getpid()}.log"
    return base


def _prune_transcripts(keep: int = 100, current: Path | None = None) -> None:
    """Prune to newest-`keep` transcripts, never deleting `current`."""
    try:
        files = sorted(log_dir().glob("install.*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    others = [p for p in files if p != current and not p.is_symlink()]
    excess = len(others) + (1 if current else 0) - keep
    for victim in others[: max(0, excess)]:
        try:
            victim.unlink()
        except OSError:
            pass


def _reassert_transcript_ownership(path: Path, uid: int, gid: int) -> None:
    # NOTE (20-B): orchestrator-side repair after sudo steps. opslog stays a
    # thin logging layer (D12 split-native); extract to a shared helper only
    # if a second caller ever needs this -- not before.
    try:
        os.chmod(path, 0o644)
        os.chown(path, uid, gid)
    except OSError as exc:
        opslog.warn(f"cannot re-assert transcript ownership: {exc}")


# ── Step contract ─────────────────────────────────────────────────────────


class Status(enum.Enum):
    OK = "OK"
    SKIP = "SKIP"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_RUN = "not-run"


@dataclass
class StepResult:
    status: Status
    changed: bool = False
    message: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class StepDef:
    key: str
    label: str
    path: Path | None  # None = not-yet-authored (owner phase has not shipped it)
    owner: str
    prerequisite: str
    needs_net: bool = False
    make_target: str | None = None  # set => run `make -C REPO_ROOT <target>` instead of path
    timeout: int = 0  # 0 = inherit SUBPROCESS_TIMEOUT


def step_table() -> list[StepDef]:
    lib = REPO_ROOT / "lib"
    return [
        StepDef("ensure_stage0", "ensure_stage0()", None, "10 / 20.1", "—"),
        StepDef("load_env", "load_env()", None, "20.1", "ensure_stage0"),
        StepDef("20-packages", "20-packages", lib / "20-packages.py", "20.3", "load_env"),
        StepDef(
            "21-store-bootstrap",
            "21-store-bootstrap",
            lib / "21-store-bootstrap.py",
            "20-F",
            "20-packages (restic+gopass)",
            needs_net=True,
        ),
        StepDef(
            "22-wifi-migrate",
            "22-wifi-migrate",
            lib / "22-wifi-migrate.sh",
            "10 Repo-changes-2 (D5)",
            "20-packages (NM installed)",
        ),
        StepDef(
            "05-home-skeleton",
            "05-home-skeleton",
            lib / "05-home-skeleton.sh",
            "15 §15.4",
            "20-packages (xdg-user-dirs-update)",
        ),
        StepDef(
            "25-searxng",
            "25-searxng",
            lib / "25-searxng.py",
            "20.3",
            "20-packages (venv), 22 (net alive)",
            needs_net=True,
        ),
        StepDef(
            "30-hardware",
            "30-hardware",
            lib / "30-hardware.sh",
            "20.3",
            "20-packages",
        ),
        StepDef(
            "35-nvidia",
            "35-nvidia",
            lib / "35-nvidia.sh",
            "20.3",
            "20-packages",
            needs_net=True,
        ),
        StepDef(
            "40-system",
            "40-system",
            lib / "40-system_config.sh",
            "20.3",
            "—",
        ),
        StepDef(
            "45-brave",
            "45-brave",
            lib / "45-brave.py",
            "20.3",
            "40-system",
        ),
        StepDef(
            "50-github",
            "50-github",
            lib / "50-github_setup.sh",
            "20.3",
            "22 (net alive)",
            needs_net=True,
        ),
        StepDef(
            "55-security",
            "55-security",
            lib / "55-security.sh",
            "20.3",
            "20-packages",
        ),
        StepDef(
            "60-ark-deploy",
            "60-ark-deploy",
            lib / "60-ark-deploy.py",
            "20.3",
            "all above (fail-closed internally)",
        ),
        StepDef(
            "61-ark-policy",
            "61-ark-policy",
            lib / "61-ark-policy.py",
            "20.3",
            "60-ark-deploy",
            needs_net=True,
            timeout=900,
        ),
        # The three deploy targets. Order matters and must track
        # `all: dotfiles dev tools` in the Makefile: dotfiles first so
        # clean-stale prunes before new files land, then dev (which deploys
        # dev/git/* → ~/.config/init/gitleaks.toml, the policy init discovers),
        # then tools (which installs ~/.local/bin/init).
        StepDef(
            "user_dotfiles",
            "user_dotfiles (make dotfiles)",
            None,
            "30",
            "05-home-skeleton",
            make_target="dotfiles",
        ),
        StepDef(
            "user_dev",
            "user_dev (make dev)",
            None,
            "30",
            "05-home-skeleton",
            make_target="dev",
        ),
        StepDef(
            "user_tools",
            "user_tools (make tools)",
            None,
            "30",
            "user_dev (init policy deployed first)",
            make_target="tools",
            # make tools builds the pi-setup zipapp and refreshes user
            # site-packages; the 300s default SUBPROCESS_TIMEOUT is tight.
            timeout=900,
        ),
    ]


def map_rc(rc: int) -> Status:
    """Delegate to the opslog reference; Status enum stays trunk-local."""
    return Status[opslog.map_rc(rc)]


def parse_result_trailer(output: str, exit_code: int) -> StepResult:
    """Map bash exit + optional RESULT {json} trailer to StepResult (20.4d).

    - warnings come from the trailer's warnings key only (never stderr-scan).
    - 3-key trailers parse (missing warnings -> []).
    - multiple RESULT lines -> last wins.
    - unknown status strings -> FAIL + warning.
    - malformed trailer -> exit-code status stands + ERROR + warning.
    """
    base = StepResult(status=map_rc(exit_code))
    lines = [ln for ln in output.splitlines() if ln.strip().startswith("RESULT ")]
    if not lines:
        return base
    raw = lines[-1][len("RESULT ") :].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        opslog.error(f"unparseable RESULT trailer: {raw!r}")
        base.warnings.append("unparseable RESULT trailer")
        return base
    if not isinstance(data, dict):
        opslog.error(f"unparseable RESULT trailer: {raw!r}")
        base.warnings.append("unparseable RESULT trailer")
        return base
    status_raw = str(data.get("status", "")).strip().upper()
    try:
        status = Status[status_raw]
        if status is Status.NOT_RUN:
            raise KeyError
    except KeyError:
        opslog.error(f"unknown RESULT status: {status_raw!r}")
        base.status = Status.FAIL
        base.warnings.append(f"unknown RESULT status: {status_raw!r}")
        return base
    changed = bool(data.get("changed", False))
    message = str(data.get("message", ""))
    warnings = data.get("warnings", [])
    if warnings is None:
        warnings = []
    if isinstance(warnings, str):
        warnings = [warnings]
    warnings = [str(w) for w in warnings]
    return StepResult(status=status, changed=changed, message=message, warnings=warnings)


# ── load_env ──────────────────────────────────────────────────────────────


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read config.txt exclusively (no config.env fallback, no .local layer).

    Split on first '=' only, strip matching quotes. D14 required-rule:
    every var in the committed file is required — empty value fails
    naming the key (unset = delete or '#'-comment the line).
    """
    cfg = path or (REPO_ROOT / CONFIG_NAME)
    if not cfg.is_file():
        raise FileNotFoundError(f"{cfg} not found")
    values: dict[str, str] = {}
    for lineno, raw_line in enumerate(cfg.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        val = raw.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not key:
            continue
        if val == "":
            raise ValueError(f"{cfg.name}: {key} is empty (line {lineno})")
        values[key] = val
    return values


def child_env(loaded: dict[str, str]) -> dict[str, str]:
    """Precedence: config.txt < real env vars < gopass (gopass via CLI, not env)."""
    env = os.environ.copy()
    for k, v in loaded.items():
        if k not in env:
            env[k] = v
    return env


# ── Stage 0 ───────────────────────────────────────────────────────────────


def print_wifi_help() -> None:
    print(
        "Manual WiFi fix (Reboot 1) — run once, then re-run tools/bootstrap.sh.\n"
        "\n"
        "Fast path (before pressing Reboot in the installer): Alt-F2,\n"
        "  cp /etc/network/interfaces /target/etc/network/interfaces, Alt-F1, Reboot.\n"
        "\n"
        "Otherwise, on first login:\n"
        "  1. ip a — find iface (wlpXs0-style, never assume wlan0).\n"
        "  2. Write /etc/network/interfaces (replace iface/SSID/pass).\n"
        "  3. sudo rfkill unblock wifi; sudo ifup <iface>\n"
        "  4. Verify: ping -c3 9.9.9.9 and ping -c3 deb.debian.org must work.\n"
        "     If not: ip a; iw dev; rfkill list; dmesg | grep -i firmware"
    )


def _wifi_diagnostics() -> None:
    for cmd in (
        ["ip", "a"],
        ["iw", "dev"],
        ["rfkill", "list"],
        ["dmesg"],
    ):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            tail = (out.stdout or "") + (out.stderr or "")
            print(f"--- {' '.join(cmd)} ---")
            print(tail[-2000:] if len(tail) > 2000 else tail)
        except Exception as exc:  # noqa: BLE001 — diagnostics must never abort the report
            print(f"--- {' '.join(cmd)} --- (failed: {exc})")


def _tcp_probe(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_stage0() -> None:
    """Abort-grade gate (D17 row 1). Fail-fast exit 1 before the step table."""
    if not require_user("install.py", "./install.py"):
        sys.exit(1)
    if not shutil.which("sudo"):
        print("ERROR: sudo not installed. Reinstall with blank root password.", file=sys.stderr)
        sys.exit(1)
    try:
        groups_out = subprocess.run(
            ["groups"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        groups_out = ""
    if "sudo" not in groups_out.split():
        print(f"ERROR: User '{os.environ.get('USER', '?')}' is not in the sudo group.", file=sys.stderr)
        print("  Run: su -c 'usermod -aG sudo <user>' && logout", file=sys.stderr)
        sys.exit(1)
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        print("ERROR: sudo not usable. Re-login, then re-run.", file=sys.stderr)
        sys.exit(1)

    missing = [p for p in STAGE0_PACKAGES if subprocess.run(["dpkg", "-s", p], capture_output=True).returncode != 0]
    if missing:
        print(f"Installing stage-0 packages: {' '.join(missing)}")
        rc = subprocess.run(["sudo", "apt-get", "install", "-y", *missing]).returncode
        if rc != 0:
            print("ERROR: stage-0 apt install failed.", file=sys.stderr)
            sys.exit(1)

    dns_ok = subprocess.run(["getent", "hosts", "deb.debian.org"], capture_output=True).returncode == 0
    tcp_ok = _tcp_probe("deb.debian.org", 443, 5.0)
    ping_ok = subprocess.run(["ping", "-c1", "-W5", "9.9.9.9"], capture_output=True).returncode == 0
    if not (dns_ok and tcp_ok and ping_ok):
        print_wifi_help()
        _wifi_diagnostics()
        sys.exit(1)

    absent = [b for b in PRESENCE_BINARIES if not shutil.which(b)]
    if absent:
        print(f"ERROR: missing after stage-0: {' '.join(absent)}", file=sys.stderr)
        sys.exit(1)


# ── Sudo keepalive ────────────────────────────────────────────────────────


def start_sudo_keepalive(stop: threading.Event) -> threading.Thread:
    def _loop() -> None:
        while not stop.wait(60):
            r = subprocess.run(["sudo", "-nv"], capture_output=True)
            if r.returncode != 0:
                retry = subprocess.run(["sudo", "-v"])
                if retry.returncode != 0:
                    opslog.error("sudo keepalive lost — re-authenticate, then re-run.")
                    os._exit(1)

    t = threading.Thread(target=_loop, name="sudo-keepalive", daemon=True)
    t.start()
    return t


# ── Reboot ────────────────────────────────────────────────────────────────


def check_stale_reboot_marker() -> None:
    """Port install.sh:79-90 verbatim (saved_boot_id == current_boot_id)."""
    marker = needs_reboot_file()
    if marker.is_file():
        try:
            saved = marker.read_text().strip()
            current = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            return
        if saved == current:
            opslog.warn("Previous reboot still pending (from this boot)")
        else:
            opslog.info("Clearing stale reboot marker (system was already rebooted)")
            try:
                marker.unlink()
            except OSError as exc:
                opslog.error(f"cannot clear stale reboot marker: {exc}")
    else:
        opslog.ok("No stale reboot marker")


_BOX_INNER = 62


def _box_line(text: str) -> None:
    """Print one reboot-box line, truncating/padding to the fixed frame."""
    print(f"  ║{text[:_BOX_INNER].ljust(_BOX_INNER)}║")


def show_reboot_prompt() -> str:
    """Port install.sh:241-287 as-is (Q8). Caller applies the 22 veto first.

    Returns "rebooted" | "declined" | "not-needed" | "failed".
    The latch is cleared only after a confirmed reboot; decline and
    non-interactive paths keep it so the next run re-prompts.
    """
    latch = needs_reboot_file().is_file()
    sys_reboot = Path("/run/reboot-required").is_file()
    if not (latch or sys_reboot):
        return "not-needed"
    opslog.warn(f"reboot prompt shown (latch={latch}, system={sys_reboot})")
    print("")
    print("  ╔══════════════════════════════════════════════════════════════╗")
    if latch and sys_reboot:
        _box_line("  REBOOT REQUIRED — NVIDIA + system updates")
    elif latch:
        _box_line("  REBOOT REQUIRED — NVIDIA driver configuration applied")
    else:
        _box_line("  REBOOT REQUIRED — kernel/system updates pending")
    _box_line("")
    if latch:
        _box_line("  NVIDIA: nvidia-smi, CUDA, NVENC will NOT work until boot.")
    if sys_reboot:
        pkgs = ""
        pkgs_file = Path("/run/reboot-required.pkgs")
        if pkgs_file.is_file():
            try:
                pkgs = ", ".join(pkgs_file.read_text().splitlines()[:3]).strip(", ")
            except OSError:
                pkgs = ""
        if pkgs:
            _box_line("  System: kernel or packages require reboot:")
            _box_line(f"    {pkgs}")
        else:
            _box_line("  System: kernel or security updates pending.")
    _box_line("")
    print("  ╚══════════════════════════════════════════════════════════════╝")
    print("")
    if sys.stdin.isatty():
        try:
            confirm = input("  Reboot now? (y/N): ").strip()
        except EOFError:
            confirm = ""
        if confirm.lower() == "y":
            rc = subprocess.run(["sudo", "systemctl", "reboot"]).returncode
            if rc != 0:
                opslog.error(f"reboot command failed (rc={rc}); latch kept")
                return "failed"
            opslog.warn("reboot confirmed by operator")
            try:
                needs_reboot_file().unlink(missing_ok=True)
            except OSError as exc:
                opslog.error(f"cannot clear reboot marker: {exc}")
            return "rebooted"
        print("  Remember to reboot later.")
        opslog.warn("reboot declined by operator; latch kept")
        return "declined"
    print("  Reboot required but running non-interactively — reboot manually.")
    opslog.warn("reboot required but non-interactive; latch kept")
    return "declined"


# ── Runner ────────────────────────────────────────────────────────────────


def normalize_key(token: str) -> str:
    t = token.strip()
    aliases = {
        # bare "5" accepted alongside "05" (only zero-padded step key).
        "05": "05-home-skeleton",
        "5": "05-home-skeleton",
        "20": "20-packages",
        "21": "21-store-bootstrap",
        "store": "21-store-bootstrap",
        "22": "22-wifi-migrate",
        "25": "25-searxng",
        "30": "30-hardware",
        "35": "35-nvidia",
        "40": "40-system",
        "45": "45-brave",
        "50": "50-github",
        "55": "55-security",
        "60": "60-ark-deploy",
        "61": "61-ark-policy",
        # Make-deploy steps: alias the target name. Deliberately NOT numeric —
        # a bare "30" would collide with 30-hardware, and these three are all
        # owner "30" but are not lib/30-hardware.sh.
        "dotfiles": "user_dotfiles",
        "dev": "user_dev",
        "tools": "user_tools",
    }
    if t in aliases:
        return aliases[t]
    return t


def run_step(defn: StepDef, env: dict[str, str], log_path: str = "") -> tuple[StepResult, float]:
    start = time.monotonic()
    cmd: list[str]
    if defn.make_target is not None:
        # Make-based deploy step. -C so the recipes' relative paths (dev/,
        # tools/, config.txt) resolve regardless of the caller's cwd.
        cmd = ["make", "-C", str(REPO_ROOT), defn.make_target]
    elif defn.path is None:
        opslog.warn(f"{defn.label}: SKIP (not-yet-authored, owner: {defn.owner})")
        return StepResult(Status.SKIP, False, f"not-yet-authored, owner: {defn.owner}"), 0.0
    elif not defn.path.is_file():
        opslog.error(f"{defn.label}: missing {defn.path} (owner {defn.owner} shipped it)")
        return StepResult(Status.FAIL, False, f"missing: {defn.path}"), 0.0
    elif defn.path.suffix == ".py":
        cmd = ["python3", str(defn.path)]
    else:
        cmd = ["bash", str(defn.path)]
    opslog.set_step(defn.label)
    timeout = defn.timeout or int(env.get("SUBPROCESS_TIMEOUT", "300"))
    try:
        rc, output = opslog.run(cmd, env=env, timeout=timeout, label=defn.label, log_path=log_path)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        opslog.error(f"{defn.label}: timed out after {timeout}s")
        return StepResult(Status.FAIL, False, "timed out"), elapsed
    result = parse_result_trailer(output, rc)
    if defn.make_target is not None and rc != 0:
        # make exits 2 on a FAILED recipe, but map_rc(2) is SKIP (the lib/
        # module convention) and SKIP counts as success in main()'s exit check.
        # Without this a broken deploy would pass the run silently.
        result.status = Status.FAIL
        result.message = f"make {defn.make_target} exited {rc}"
    elapsed = time.monotonic() - start
    if result.status is Status.OK:
        opslog.ok(f"{defn.label} OK ({elapsed:.0f}s)")
    elif result.status is Status.SKIP:
        opslog.warn(f"{defn.label} SKIP: {result.message}")
    elif result.status is Status.PARTIAL:
        opslog.warn(f"{defn.label} PARTIAL: {result.message}")
    else:
        opslog.error(f"{defn.label} FAIL: {result.message or 'see log'}")
    for w in result.warnings:
        opslog.warn(f"{defn.label} warning: {w}")
    return result, elapsed


def print_step_table(steps: list[StepDef]) -> None:
    step_w = max([18, *(len(s.label) for s in steps)]) + 2
    print(f"  {'STEP':<{step_w}}{'OWNER':<26}PREREQUISITE")
    for s in steps:
        print(f"  {s.label:<{step_w}}{s.owner:<26}{s.prerequisite}")
    print(f"  {'reboot prompt':<{step_w}}{'20.1':<26}all above")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="install.py trunk (20-A skeleton)")
    opslog.add_cli_args(p)
    p.add_argument("--only", default="", help="comma-separated step keys to run")
    p.add_argument("--skip", default="", help="comma-separated step keys to skip")
    p.add_argument("--dry-run", action="store_true", help="print step table only")
    return p


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < PYTHON_FLOOR:
        print("ERROR: python3 >= 3.11 required — run tools/bootstrap.sh", file=sys.stderr)
        return 1

    parser = build_parser()
    args = parser.parse_args(argv)

    steps = step_table()

    only = {normalize_key(t) for t in args.only.split(",") if t.strip()}
    skip = {normalize_key(t) for t in args.skip.split(",") if t.strip()}

    valid_keys = sorted(s.key for s in steps)
    unknown = sorted((only | skip) - set(valid_keys))
    if unknown:
        print(
            f"ERROR: unknown --only/--skip key(s): {', '.join(unknown)} "
            f"(valid keys: {', '.join(valid_keys)})",
            file=sys.stderr,
        )
        return 2

    def _warn(msg: str) -> None:
        try:
            opslog.warn(msg)
        except Exception:  # noqa: BLE001 — dry-run has no logger; fall back to stdout
            pass
        if args.dry_run:
            print(f"  WARN {msg}")

    def _dependent_warnings() -> None:
        if "22-wifi-migrate" in only or "22-wifi-migrate" not in skip:
            if "20-packages" in skip:
                _warn("skipping 20-packages with 22-wifi-migrate selected (NM may be missing)")
        for dependent in ("user_dotfiles", "user_dev", "user_tools"):
            if dependent in only and "05-home-skeleton" in skip:
                _warn(f"{dependent} selected with 05-home-skeleton skipped (target dirs may be missing)")

    if args.dry_run:
        _dependent_warnings()
        print_step_table(steps)
        return 0

    # Transcript lifecycle (20-B): per-run file, atomic latest, prune-100.
    uid, gid = os.getuid(), os.getgid()
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        os.chmod(log_dir(), 0o755)
    except OSError as exc:
        print(f"ERROR: cannot create log dir: {exc}", file=sys.stderr)
        return 1
    mode = "a" if args.log_file else "w"
    try:
        transcript = Path(args.log_file) if args.log_file else transcript_path("full")
        transcript.touch(exist_ok=True)
        os.chmod(transcript, 0o644)
        os.chown(transcript, uid, gid)
        if not args.log_file:
            latest = log_dir() / "latest"
            tmp_link = log_dir() / f".latest.{os.getpid()}.tmp"
            if tmp_link.is_symlink() or tmp_link.exists():
                tmp_link.unlink()
            tmp_link.symlink_to(transcript.name)
            os.replace(tmp_link, latest)
    except OSError as exc:
        print(f"ERROR: cannot write transcript: {exc}", file=sys.stderr)
        return 1
    print(f"  Transcript: {transcript}")
    try:
        if args.quiet:
            level: int | str = "ERROR"
        elif args.verbose:
            level = "DEBUG"
        else:
            level = opslog.OK
        opslog.configure(
            component="install",
            file=str(transcript),
            mode=mode,
            terminal_level=level,
            ownership="user",
        )
    except OSError as exc:
        print(f"ERROR: cannot write transcript: {exc}", file=sys.stderr)
        return 1
    if not args.log_file:
        _prune_transcripts(keep=100, current=transcript)

    # ensure_stage0 + load_env always run regardless of --only/--skip.
    try:
        ensure_stage0()
    except SystemExit as exc:
        return int(exc.code or 1)
    opslog.ok("stage-0 OK")

    check_stale_reboot_marker()

    try:
        loaded = load_env()
    except (FileNotFoundError, ValueError) as exc:
        opslog.error(str(exc))
        return 1
    opslog.ok("config.txt validated")
    env = child_env(loaded)
    # 20-C Q1-A: orchestrator marker — shared transcript for bash children.
    # Ephemeral per-run path, never stored in config.txt (D14).
    env["INSTALL_TRANSCRIPT"] = str(transcript)
    # 30: make steps must never block on the clean-stale prompt — this run has
    # no TTY, so `make dotfiles` would otherwise abort on any orphan found.
    # YES=1 is the documented non-interactive opt-in (Makefile:27).
    env["YES"] = "1"

    # Dependent warnings (extends :51-54 logic).
    _dependent_warnings()

    stop = threading.Event()
    start_sudo_keepalive(stop)
    atexit.register(stop.set)
    atexit.register(lambda: subprocess.run(["sudo", "-k"], capture_output=True))

    # Runner order: no packages/-binary step runs before 20-packages passes
    # (enforced via per-module guards + needs_net skip below; see D16/D17).
    results: dict[str, StepResult] = {}
    runnable = [s for s in steps if s.key not in ("ensure_stage0", "load_env")]
    results["ensure_stage0"] = StepResult(Status.OK, False, "converged")
    results["load_env"] = StepResult(Status.OK, False, "validated")

    if only:
        runnable = [s for s in runnable if s.key in only]
    if skip:
        runnable = [s for s in runnable if s.key not in skip]

    wifi_failed = False
    elapsed_map: dict[str, float] = {"ensure_stage0": 0.0, "load_env": 0.0}
    total = len(runnable)
    for i, defn in enumerate(runnable, 1):
        opslog.progress(i, total)
        if defn.needs_net and wifi_failed:
            opslog.warn(f"{defn.label}: SKIP (blocked: step-22 failed)")
            results[defn.key] = StepResult(Status.SKIP, False, "blocked: step-22 failed")
            elapsed_map[defn.key] = 0.0
            continue
        try:
            res, elapsed = run_step(defn, env, log_path=str(transcript))
        except OSError as exc:
            print(f"ERROR: log write failed ({exc}) -- aborting, transcript truncated", file=sys.stderr)
            return 1
        _reassert_transcript_ownership(transcript, uid, gid)
        results[defn.key] = res
        elapsed_map[defn.key] = elapsed
        if defn.key == "22-wifi-migrate" and res.status is Status.FAIL:
            wifi_failed = True

    # Steps filtered out by --only/--skip are not-run (summary answers what ran).
    for s in steps:
        if s.key not in results:
            results[s.key] = StepResult(Status.NOT_RUN, False, "filtered by --only/--skip")

    print("")
    rows = [
        (s.label, results[s.key].status.value, elapsed_map.get(s.key, 0.0), results[s.key].message)
        for s in steps
    ]
    opslog.summary(rows, log_path=str(transcript))

    # Reboot veto (D16): 22 FAIL suppresses the prompt run-wide, overrides latch.
    if wifi_failed:
        opslog.error("WIFI UNMANAGED — reboot prompt suppressed. Fix WiFi, then re-run.")
        print("  WiFi fix hint: check interfaces backup interfaces.bak-* and NM profile, then re-run.")
        return 1

    show_reboot_prompt()
    return 0 if all(r.status in (Status.OK, Status.SKIP, Status.NOT_RUN) for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
