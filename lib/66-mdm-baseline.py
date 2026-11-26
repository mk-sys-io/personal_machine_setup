#!/usr/bin/env python3
"""MDM baseline — kiosk secret, seed-password scrub, lifecycle guard (Phase B3).

Convergent duties:
1. Ensure ~/.config/mdm/secrets.env carries MDM_KIOSK_PASSWORD (16 hex chars,
   short enough to type on the phone's kiosk-exit prompt) and that NO row in
   the hmdm `configurations` table keeps the public seed default 12345678.
2. Re-check the committed policy data (etc/mdm/policy/policy.json) via the
   shared mdm_policy_data helper: valid JSON, required keys, and anti-coupling
   (D10) — no serial/IMEI keys or 15-digit numbers anywhere in the data.

Policy is applied to the server by `lib/67 --enroll`; this module validates
the data via `mdm_policy_data`. Lifecycle (tomcat10 + postgresql auto-start on
boot) is asserted enabled — never started here, per the on-demand model (D12).

Wired into install.py as 66-mdm-baseline

Exit codes:
    0  converged
    1  failure
    2  skip (in VM)
    3  partial (not converged)
Stdlib only.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from mdm_policy_data import POLICY_JSON, anti_coupling_violations, load_policy  # noqa: E402
from preconditions import require_user  # noqa: E402


# ── log facades ──────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(msg, flush=True)


def log_step(msg: str) -> None:
    print(f">>> {msg}", flush=True)


def log_ok(msg: str) -> None:
    print(f"  OK    {msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"  WARN  {msg}", file=sys.stderr, flush=True)


def log_error(msg: str) -> None:
    print(f"  ERROR {msg}", file=sys.stderr, flush=True)


def _result(status: str, changed: bool, message: str) -> None:
    print(
        f'RESULT {{"status": "{status}", "changed": {str(changed).lower()}, '
        f'"message": {json.dumps(message)}}}'
    )


class UsageError(RuntimeError):
    """Bad invocation: usage printed to stderr; main() maps it to exit 1."""


def _usage_error(msg: str) -> UsageError:
    rerun = " ".join(["python3", "lib/66-mdm-baseline.py", *sys.argv[1:]])
    log_error(f"{msg} — rerun: {rerun}")
    print(USAGE, file=sys.stderr)
    return UsageError(msg)


USAGE = """Usage: python3 lib/66-mdm-baseline.py [--verify]

    (default run)   generate the kiosk secret, scrub the seed kiosk password,
                    and enable tomcat10 + postgresql at boot
    --verify        convergence check only; changes nothing

Both paths need a cached sudo ticket (run `sudo -v` first).
Example: python3 lib/66-mdm-baseline.py --verify
"""

_ALLOWED_FLAGS = {"-h", "--help", "--verify"}


def _validate_args(args: list[str]) -> None:
    for token in args:
        if token not in _ALLOWED_FLAGS:
            raise _usage_error(f"unknown argument: {token}")


# ── constants ────────────────────────────────────────────────────────────────
SECRETS = Path.home() / ".config" / "mdm" / "secrets.env"

SEED_KIOSK_PASSWORD = "12345678"
KIOSK_SECRET_KEY = "MDM_KIOSK_PASSWORD"

LIFECYCLE_UNITS = ("tomcat10", "postgresql")

TIMER_UNIT_RE = re.compile(r"(?i)\b(?:h?mdm|mdm-le|lego)\b")


# ── helpers ──────────────────────────────────────────────────────────────────
def is_vm() -> bool:
    return subprocess.run(["systemd-detect-virt", "-q"], check=False).returncode == 0


def _run_priv(*args: str, input_text: str | None = None) -> tuple[int, str]:
    p = subprocess.run(
        ["sudo", "-n", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _read_secret(key: str) -> str | None:
    if not SECRETS.is_file():
        return None
    for line in SECRETS.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    return None


def _ensure_secret(key: str, value: str) -> None:
    """Append key=value to secrets.env (0600). Only called when key absent."""
    lines = SECRETS.read_text().splitlines() if SECRETS.is_file() else []
    lines = [ln for ln in lines if ln.strip() and not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    SECRETS.write_text("\n".join(lines) + "\n")
    SECRETS.chmod(0o600)


# ── kiosk exit password ──────────────────────────────────────────────────────
def ensure_kiosk_password() -> bool:
    """Ensure MDM_KIOSK_PASSWORD exists in secrets.env; never logged."""
    pw = _read_secret(KIOSK_SECRET_KEY)
    if pw:
        log_ok("kiosk exit password secret present")
        return False
    log_step("generating kiosk exit password into secrets.env")
    _ensure_secret(KIOSK_SECRET_KEY, secrets.token_hex(8))
    log_ok("kiosk exit password generated (read secrets.env into a password manager)")
    return True


def scrub_seed_kiosk_password() -> bool:
    """Replace any configurations.password still at the public seed default.

    Runs the UPDATE as postgres via stdin: the new value travels only through
    the psql variable (`\\set`), never argv, never output.
    """
    rc, out = _run_priv(
        "-u",
        "postgres",
        "psql",
        "hmdm",
        "-At",
        "-c",
        f"SELECT count(*) FROM configurations WHERE password = '{SEED_KIOSK_PASSWORD}';",
    )
    if rc != 0:
        raise RuntimeError(f"kiosk password count query failed: {out.strip()}")
    n = int(out.strip().splitlines()[-1]) if out.strip() else 0
    if n == 0:
        log_ok("no seed kiosk password remains in configurations")
        return False

    pw = _read_secret(KIOSK_SECRET_KEY)
    if not pw:
        raise RuntimeError("seed password found but no MDM_KIOSK_PASSWORD secret")
    sql = (
        f"\\set pw {pw}\n"
        f"UPDATE configurations SET password = :'pw' "
        f"WHERE password = '{SEED_KIOSK_PASSWORD}';\n"
    )
    log_step(f"replacing seed kiosk password on {n} configuration(s)")
    rc, out = _run_priv(
        "-u", "postgres", "psql", "hmdm", "-v", "ON_ERROR_STOP=1", input_text=sql
    )
    if rc != 0:
        raise RuntimeError(f"kiosk password scrub failed: {out.strip()}")
    rc, out = _run_priv(
        "-u",
        "postgres",
        "psql",
        "hmdm",
        "-At",
        "-c",
        f"SELECT count(*) FROM configurations WHERE password = '{SEED_KIOSK_PASSWORD}';",
    )
    if rc != 0 or (out.strip().splitlines()[-1] if out.strip() else "1") != "0":
        raise RuntimeError("kiosk password scrub did not converge")
    log_ok("seed kiosk password scrubbed")
    return True


# ── lifecycle ────────────────────────────────────────────────────────────────
def ensure_units_enabled() -> bool:
    """Assert tomcat10 + postgresql are enabled at boot (owner decision)."""
    rc, out = _run_priv("systemctl", "is-enabled", *LIFECYCLE_UNITS)
    if rc == 0:
        log_ok("lifecycle units enabled at boot (tomcat10, postgresql)")
        return False
    log_step("enabling lifecycle units at boot")
    rc, out = _run_priv("systemctl", "enable", *LIFECYCLE_UNITS)
    if rc != 0:
        raise RuntimeError(f"systemctl enable failed: {out.strip()}")
    log_ok("lifecycle units enabled (tomcat10, postgresql)")
    return True


# ── convergence / verify checks ──────────────────────────────────────────────
def _cron_entries() -> list[str]:
    """MDM-relevant cron lines from the operator crontab and /etc/cron.d."""
    entries: list[str] = []
    p = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if p.returncode == 0:
        entries.extend(
            ln.strip()
            for ln in (p.stdout or "").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        )
    cron_d = Path("/etc/cron.d")
    if cron_d.is_dir():
        for path in sorted(cron_d.iterdir()):
            try:
                for ln in path.read_text(errors="replace").splitlines():
                    if ln.strip() and not ln.lstrip().startswith("#"):
                        entries.append(f"{path.name}: {ln.strip()}")
            except OSError:
                continue
    return entries


def mdm_timers() -> tuple[bool, list[str], list[str]]:
    """(scan_ok, violations, notes) across system + user timers and cron.

    A missing user bus is 'not checked' (note), never a failure; a missing
    system manager listing is a failure. The certbot cron is exempt by
    construction — the regex only matches MDM/lego renewal names.
    """
    violations: list[str] = []
    notes: list[str] = []
    scopes = (
        (
            "system",
            ["systemctl", "list-unit-files", "--type=timer", "--no-pager", "--plain"],
        ),
        (
            "user",
            [
                "systemctl",
                "--user",
                "list-unit-files",
                "--type=timer",
                "--no-pager",
                "--plain",
            ],
        ),
    )
    for label, cmd in scopes:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if p.returncode != 0:
            if label == "system":
                return (
                    False,
                    violations,
                    [
                        f"could not list system timer units: {(p.stderr or p.stdout).strip()}"
                    ],
                )
            notes.append(f"user timer units not checked ({label} bus unavailable)")
            continue
        violations.extend(
            ln.strip()
            for ln in (p.stdout or "").splitlines()
            if TIMER_UNIT_RE.search(ln)
        )
    violations.extend(e for e in _cron_entries() if TIMER_UNIT_RE.search(e))
    return True, violations, notes


def converged() -> tuple[bool, list[str]]:
    ok = True
    msgs: list[str] = []

    pw = _read_secret(KIOSK_SECRET_KEY)
    if pw:
        msgs.append("kiosk exit password secret present")
    else:
        msgs.append("MDM_KIOSK_PASSWORD missing from secrets.env")
        ok = False

    rc, out = _run_priv(
        "-u",
        "postgres",
        "psql",
        "hmdm",
        "-At",
        "-c",
        f"SELECT count(*) FROM configurations WHERE password = '{SEED_KIOSK_PASSWORD}';",
    )
    if rc != 0:
        msgs.append(f"configurations password check failed: {out.strip()}")
        ok = False
    else:
        n = int(out.strip().splitlines()[-1]) if out.strip() else -1
        msgs.append(
            "no seed kiosk password in configurations"
            if n == 0
            else f"{n} configuration(s) still carry the seed password"
        )
        ok &= n == 0

    try:
        policy = load_policy()
        msgs.append(f"policy data valid ({POLICY_JSON.name})")
        violations = anti_coupling_violations(policy)
        if violations:
            msgs.append(f"anti-coupling violations in policy data: {violations}")
            ok = False
        else:
            msgs.append("policy data free of serial/IMEI keying (D10)")
    except RuntimeError as e:
        msgs.append(str(e))
        ok = False

    rc, _ = _run_priv("systemctl", "is-enabled", *LIFECYCLE_UNITS)
    if rc == 0:
        msgs.append("lifecycle units enabled at boot (tomcat10, postgresql)")
    else:
        msgs.append("lifecycle units not enabled at boot")
        ok = False

    scan_ok, violations, notes = mdm_timers()
    msgs.extend(notes)
    if not scan_ok:
        ok = False
    elif violations:
        msgs.append(f"unexpected MDM timer/schedule entries: {violations}")
        ok = False
    else:
        msgs.append("no MDM timer units (on-demand model intact)")

    return ok, msgs


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print(USAGE)
        return 0

    try:
        _validate_args(argv[1:])
    except UsageError as exc:
        _result("FAIL", False, str(exc))
        return 1

    if not require_user("lib/66-mdm-baseline.py", "python3 lib/66-mdm-baseline.py"):
        return 1

    if is_vm():
        log_step("66-mdm-baseline: running in VM, skipping")
        _result("SKIP", False, "in VM")
        return 2

    try:
        if "--verify" in argv[1:]:
            if _run_priv("true")[0] != 0:
                log_error(
                    "no cached sudo ticket — run `sudo -v` (or ./install.py --only 66) "
                    "in your terminal, then retry --verify"
                )
                _result("FAIL", False, "sudo unavailable for --verify")
                return 1
            ok, msgs = converged()
            for m in msgs:
                log(f"  {m}")
            return 0 if ok else 3

        if not SECRETS.is_file():
            raise RuntimeError(
                f"secrets file not found: {SECRETS} — run lib/63-mdm-server.py first"
            )

        changed = False
        changed |= ensure_kiosk_password()
        changed |= scrub_seed_kiosk_password()
        changed |= ensure_units_enabled()

        ok, msgs = converged()
        for m in msgs:
            log(f"  {m}")
        if ok:
            _result("OK", changed, "mdm-policy converged")
            return 0
        else:
            log_warn("convergence check failed")
            _result("FAIL", changed, "post-scrub verification failed")
            return 1

    except Exception as exc:
        log_error(f"66-mdm-baseline: {exc}")
        _result("FAIL", False, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
