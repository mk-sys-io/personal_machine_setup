#!/usr/bin/env python3
"""MDM policy — policy-as-data guard + kiosk exit-password scrub (Phase B3).

Two convergent duties:
1. Ensure ~/.config/mdm/secrets.env carries MDM_KIOSK_PASSWORD (16 hex chars,
   short enough to type on the phone's kiosk-exit prompt) and that NO row in
   the hmdm `configurations` table keeps the public seed default 12345678.
2. Verify the committed policy data file (etc/mdm/policy/policy.json): valid
   JSON, required keys, and anti-coupling (D10) — no serial/IMEI keys or
   15-digit numbers anywhere in the data.

Policy itself is applied to the server/phone in Phases D1/D2 (gated on the
DG-3/DG-4 on-device checks); this module only guards the seed state and the
data. Lifecycle (tomcat10 + postgresql auto-start on boot) is asserted
enabled — never started here, per the on-demand model (D12).

Run standalone: python3 lib/66-mdm-policy.py
Wired into install.py as 66-mdm-policy

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
    safe = message.replace('"', "'").replace("\n", " ")
    print(
        f'RESULT {{"status": "{status}", "changed": {str(changed).lower()}, "message": "{safe}"}}'
    )


# ── constants ────────────────────────────────────────────────────────────────
SECRETS = Path.home() / ".config" / "mdm" / "secrets.env"
POLICY_JSON = REPO_ROOT / "etc" / "mdm" / "policy" / "policy.json"

SEED_KIOSK_PASSWORD = "12345678"
KIOSK_SECRET_KEY = "MDM_KIOSK_PASSWORD"

REQUIRED_POLICY_KEYS = (
    "schema",
    "whitelist_apps",
    "agent_app_settings",
    "restrictions",
    "chrome",
    "keep_system_apps",
)

# D10 anti-coupling: never key on serial/IMEI. A 15-digit run is IMEI-shaped.
# serial/imei match as substrings: \bserial\b misses keys like device_serial
# ("_" is a word char); no plausible false positive in policy data.
ANTICOUPLING_RE = re.compile(r"(?i)serial|imei|\b\d{15}\b")

# Documented prose fields (the file's own explanation) may legitimately use
# the word "serial"; D10 forbids serial/IMEI *data* (keys or values).
PROSE_KEYS = frozenset({"comment", "note"})

LIFECYCLE_UNITS = ("tomcat10", "postgresql")


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


# ── kiosk exit password (the one live-system change this module makes) ──────
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


# ── policy data file ─────────────────────────────────────────────────────────
def load_policy() -> dict:
    """Load and validate etc/mdm/policy/policy.json (fails loud on drift)."""
    if not POLICY_JSON.is_file():
        raise RuntimeError(f"policy data file missing: {POLICY_JSON}")
    try:
        policy = json.loads(POLICY_JSON.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"policy data file unreadable: {e}") from e
    missing = [k for k in REQUIRED_POLICY_KEYS if k not in policy]
    if missing:
        raise RuntimeError(f"policy data missing keys: {', '.join(missing)}")
    return policy


def anti_coupling_violations(policy: dict) -> list[str]:
    """Scan policy data (dict keys + values), exempting prose fields.

    A serial/IMEI join key (D10 violation) would appear as a dict key or a
    value; free-text comment/note fields are documentation, not data.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                found.update(ANTICOUPLING_RE.findall(str(k)))
                if k not in PROSE_KEYS:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            found.update(ANTICOUPLING_RE.findall(node))

    walk(policy)
    return sorted(found)


# ── lifecycle (auto-start on boot kept; never started/stopped here) ─────────
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
def converged() -> tuple[bool, list[str]]:
    ok = True
    msgs: list[str] = []

    # a) kiosk password secret
    pw = _read_secret(KIOSK_SECRET_KEY)
    if pw:
        msgs.append("kiosk exit password secret present")
    else:
        msgs.append("MDM_KIOSK_PASSWORD missing from secrets.env")
        ok = False

    # b) no seed default anywhere
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

    # c) policy data valid + anti-coupling
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

    # d) lifecycle units enabled at boot
    rc, _ = _run_priv("systemctl", "is-enabled", *LIFECYCLE_UNITS)
    if rc == 0:
        msgs.append("lifecycle units enabled at boot (tomcat10, postgresql)")
    else:
        msgs.append("lifecycle units not enabled at boot")
        ok = False

    # e) no MDM timer units (on-demand only, D12). Trailing \b keeps the
    # kernel's mdmonitor* timers from false-matching (\bmdm alone does).
    rc, out = _run_priv(
        "systemctl", "list-unit-files", "--type=timer", "--no-pager", "--plain"
    )
    if rc == 0 and not re.search(r"(?i)\bmdm\b", out):
        msgs.append("no MDM timer units (on-demand model intact)")
    else:
        msgs.append(
            "unexpected MDM timer unit present"
            if rc == 0
            else "could not list timer units"
        )
        ok = False

    return ok, msgs


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Usage: python3 lib/66-mdm-policy.py [--verify]")
        return 0

    if not require_user("lib/66-mdm-policy.py", "python3 lib/66-mdm-policy.py"):
        return 1

    if is_vm():
        log_step("66-mdm-policy: running in VM, skipping")
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

        # secrets.env must exist (created by 63-mdm-server)
        if not SECRETS.is_file():
            raise RuntimeError(
                f"secrets file not found: {SECRETS} — run lib/63-mdm-server.py first"
            )

        changed = False
        changed |= ensure_kiosk_password()
        changed |= scrub_seed_kiosk_password()
        changed |= ensure_units_enabled()

        # Final convergence check
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
        log_error(f"66-mdm-policy: {exc}")
        _result("FAIL", False, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
