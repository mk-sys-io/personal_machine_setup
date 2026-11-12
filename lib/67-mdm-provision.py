#!/usr/bin/env python3
"""MDM provisioning — enrollment pre-flight (Phase C1).

Laptop-side provisioning for the ADB-primary device-owner enrollment (DG-6):

1. Stage the official Headwind agent APK (size + sha256 pinned; never
   recompiled — Play Protect blocks custom builds).
2. Verify the TLS chain served on 443 is complete AND anchored at
   ISRG Root X1 (live 2026-11-12: leaf <- YR2 <- Root YR cross-signed by
   X1 = LE's Default chain for YR2). The alternate chain ends at a root
   no major trust store carries — fatal for the phone's frozen Android 11
   store, hence the --preferred-chain pin in 64's renewal wrapper.

Device-side flows live ONLY behind explicit flags (the default trunk run
never touches a phone):
  --enroll           guided ADB device-owner enrollment (C2, interactive)
  --capture [--dry-run]
                     post-enrollment profile capture (context.md §3.6)

Enroll preflight asserts the runtime facts that matter for enrollment
(units ACTIVE now, panel serving over HTTPS, chain anchored) — not the
full --verify of 63/64/66, whose exit codes bundle unrelated checks.
Capture is phone-side only and must work with the server down.

Run standalone: python3 lib/67-mdm-provision.py
Wired into install.py as 67-mdm-provision

Exit codes:
    0  converged
    1  failure
    2  skip (in VM)
    3  partial (dpm refusal → QR fallback indicated / not converged)
Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
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
APK_URL = "https://h-mdm.com/files/hmdm-6.40-os.apk"
APK_SHA256 = "068b5870213c2e63df6f7a44c781d5152371f10da2eb8c849d08fb6d074d84ba"
APK_SIZE = 6_526_705

CACHE_DIR = Path.home() / ".cache" / "mdm"
STATE_DIR = Path.home() / ".local" / "state" / "mdm"

REQUIRED_PKGS = ("android-sdk-platform-tools",)

AGENT_PKG = "com.hmdm.launcher"
RECEIVER = f"{AGENT_PKG}/.AdminReceiver"
INIT_JSON_ON_DEVICE = (
    "/storage/emulated/0/Android/data/com.hmdm.launcher/files/init.json"
)


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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_domain() -> str:
    sys.path.insert(0, str(REPO_ROOT))
    from install import load_env  # noqa: E402

    d = load_env().get("MDM_DOMAIN", "").strip().lower()
    if not d:
        raise RuntimeError("MDM_DOMAIN not set in config.txt")
    if not re.match(r"^[a-z0-9.-]+\.duckdns\.org$", d):
        raise RuntimeError(f"MDM_DOMAIN must be a duckdns.org subdomain, got: {d}")
    return d


# ── apt prerequisites verification (never installs) ─────────────────────────
def verify_prereqs() -> None:
    # dpkg-query is a read-only query — deliberately NOT under sudo: a missing
    # sudo ticket must not masquerade as a missing package (64/63 route this
    # through sudo -n and fail confusingly without a ticket).
    missing = []
    for pkg in REQUIRED_PKGS:
        p = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg],
            capture_output=True,
            text=True,
            check=False,
        )
        if p.returncode != 0 or "install ok installed" not in p.stdout:
            missing.append(pkg)
    if missing:
        raise RuntimeError(
            f"missing packages: {', '.join(missing)} — "
            f"run ./install.py --only 20 or add them to packages/apt.txt"
        )


# ── agent APK staging (pinned, never auto-replaced) ─────────────────────────
def stage_apk() -> bool:
    apk = CACHE_DIR / Path(APK_URL).name
    if apk.is_file():
        if apk.stat().st_size == APK_SIZE and _sha256_file(apk) == APK_SHA256:
            log_ok(f"agent APK staged ({apk.name}, sha256 ok)")
            return False
        raise RuntimeError(
            f"staged APK pin mismatch: {apk} — upstream may have re-released "
            "6.40; re-verify the pin manually before replacing it"
        )
    log_step(f"downloading agent APK: {APK_URL}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = apk.with_suffix(".part")
    try:
        with urllib.request.urlopen(APK_URL, timeout=120) as resp, tmp.open("wb") as f:
            for chunk in iter(lambda: resp.read(65536), b""):
                f.write(chunk)
        if tmp.stat().st_size != APK_SIZE:
            raise RuntimeError(f"download size mismatch: {tmp.stat().st_size}")
        if _sha256_file(tmp) != APK_SHA256:
            raise RuntimeError(
                "download sha256 mismatch — do NOT use this file; re-verify "
                "the official 6.40 release and its hash manually"
            )
        tmp.replace(apk)
    finally:
        tmp.unlink(missing_ok=True)
    log_ok(f"agent APK staged and pin-verified ({apk.name})")
    return True


# ── TLS chain check (complete AND anchored at ISRG Root X1) ─────────────────
def _split_pems(text: str) -> list[str]:
    blocks: list[str] = []
    cur: list[str] = []
    inside = False
    for line in text.splitlines():
        if "-----BEGIN CERTIFICATE-----" in line:
            inside = True
            cur = [line]
        elif inside:
            cur.append(line)
            if "-----END CERTIFICATE-----" in line:
                blocks.append("\n".join(cur) + "\n")
                inside = False
    return blocks


def check_chain(domain: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(
            [
                "openssl",
                "s_client",
                "-showcerts",
                "-connect",
                f"{domain}:443",
                "-servername",
                domain,
            ],
            input="",
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return False, f"443 handshake with {domain} timed out"
    blocks = _split_pems(p.stdout)
    if not blocks:
        return False, f"no certificates served on 443 for {domain}"
    if len(blocks) < 3:
        return False, (
            f"incomplete chain: {len(blocks)} cert(s) served, need >= 3 "
            "(leaf + intermediate + anchoring root) — the documented "
            "'Trust anchor for certification path not found' signature"
        )
    q = subprocess.run(
        ["openssl", "x509", "-noout", "-issuer"],
        input=blocks[-1],
        capture_output=True,
        text=True,
        check=False,
    )
    issuer = (q.stdout or "").strip()
    if "ISRG Root X1" not in issuer:
        return False, (
            "chain does not anchor at ISRG Root X1 (top cert issuer: "
            f"{issuer or 'unreadable'}) — the alternate Root-YR chain is "
            "not trusted by the phone's frozen Android 11 store"
        )
    q = subprocess.run(
        ["openssl", "x509", "-checkend", "0", "-noout"],
        input=blocks[0],
        capture_output=True,
        text=True,
        check=False,
    )
    if q.returncode != 0:
        return False, "leaf certificate expired or invalid"
    return True, (
        f"complete chain of {len(blocks)} certs served on 443, "
        "anchored at ISRG Root X1, leaf valid"
    )


# ── runtime facts that actually gate enrollment (not full --verify) ─────────
def https_panel_probe(domain: str) -> tuple[bool, str]:
    """Panel must serve over HTTPS via the domain (B2 /etc/hosts resolves it)."""
    try:
        with urllib.request.urlopen(f"https://{domain}/", timeout=10) as resp:
            code = resp.getcode()
            if code not in (200, 302):
                return False, f"https 443 returned {code}"
            body = resp.read(65536).decode("utf-8", "replace")
            if "MDM web panel" not in body:
                return False, "https 443 does not serve the MDM web panel"
            return True, f"https 443 serves the MDM web panel ({code})"
    except Exception as e:
        return False, f"https 443 failed: {e}"


def units_active() -> tuple[bool, str]:
    """tomcat10 + postgresql must be ACTIVE now (enabled-at-boot is not enough)."""
    p = subprocess.run(
        ["systemctl", "is-active", "tomcat10", "postgresql"],
        capture_output=True,
        text=True,
        check=False,
    )
    states = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    if p.returncode == 0 and states == ["active", "active"]:
        return True, "tomcat10 and postgresql active"
    return False, f"units not both active: {states or p.returncode}"


# ── convergence (--verify) ──────────────────────────────────────────────────
def converged() -> tuple[bool, list[str]]:
    msgs: list[str] = []
    ok = True

    try:
        verify_prereqs()
        msgs.append("adb present (android-sdk-platform-tools)")
    except RuntimeError as e:
        msgs.append(str(e))
        ok = False

    apk = CACHE_DIR / Path(APK_URL).name
    if (
        apk.is_file()
        and apk.stat().st_size == APK_SIZE
        and _sha256_file(apk) == APK_SHA256
    ):
        msgs.append("agent APK staged (sha256 ok)")
    else:
        msgs.append(f"agent APK missing or pin mismatch: {apk}")
        ok = False

    try:
        chain_ok, msg = check_chain(load_domain())
        msgs.append(msg)
        ok &= chain_ok
    except Exception as e:
        msgs.append(f"chain check failed: {e}")
        ok = False

    return ok, msgs


# ── adb helpers ──────────────────────────────────────────────────────────────
def _adb(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", *args], capture_output=True, text=True, check=False, timeout=timeout
    )


def _adb_devices() -> list[tuple[str, str]]:
    p = _adb("devices")
    rows: list[tuple[str, str]] = []
    for line in (p.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        serial, _, state = line.partition("\t")
        rows.append((serial.strip(), state.strip()))
    return rows


def _one_device() -> tuple[str, str]:
    """Exactly one device in 'device' state, else raise with the fix."""
    rows = _adb_devices()
    if not rows:
        raise RuntimeError(
            "no device attached — actual enrollment happens at C2 with the "
            "phone connected over USB (C1 tests refusal only)"
        )
    if len(rows) > 1:
        raise RuntimeError(f"multiple devices attached: {rows} — attach exactly one")
    serial, state = rows[0]
    if state == "unauthorized":
        raise RuntimeError(
            "device unauthorized — accept the RSA debugging prompt on the "
            "phone, then retry"
        )
    if state != "device":
        raise RuntimeError(f"device {serial} is in state '{state}', not 'device'")
    return serial, state


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N]: ").strip().lower() == "y"
    except EOFError:
        return False


def _shell_out(cmd: subprocess.CompletedProcess, what: str) -> None:
    """Raise when a phone-side command fails, showing its stderr/stdout."""
    if cmd.returncode != 0:
        detail = (cmd.stderr or cmd.stdout or "").strip()
        raise RuntimeError(f"{what} failed (rc={cmd.returncode}): {detail}")


# ── --enroll: guided ADB device-owner enrollment (C2, interactive) ───────────
def run_enroll(init_json: str | None) -> int:
    # Preflight gates — each failure exits before any phone-side mutation.
    log_step("enroll preflight: laptop-side runtime facts")
    verify_prereqs()
    log_ok("adb present")
    stage_apk()

    domain = load_domain()
    for probe in (units_active(), https_panel_probe(domain), check_chain(domain)):
        if not probe[0]:
            raise RuntimeError(f"preflight gate failed: {probe[1]}")
        log_ok(probe[1])

    log_step("enroll preflight: device gates")
    _one_device()
    p = _adb("shell", "settings", "get", "secure", "user_setup_complete")
    val = (p.stdout or "").strip()
    if val != "0":
        raise RuntimeError(
            f"user_setup_complete={val!r} — device is provisioned; factory "
            "reset is required before device-owner can be granted (D10)"
        )
    log_ok("device unprovisioned (user_setup_complete=0)")

    p = _adb("shell", "getprop", "ro.build.fingerprint")
    fp = (p.stdout or "").strip()
    log(f"  device fingerprint: {fp}")
    log(
        f"  android: {_adb('shell', 'getprop', 'ro.build.version.release').stdout.strip()} "
        f"(API {_adb('shell', 'getprop', 'ro.build.version.sdk').stdout.strip()})"
    )

    # Stage 1-2: install the pinned agent APK
    if not _confirm("Install the staged agent APK now?"):
        log("aborted by owner")
        return 1
    _shell_out(_adb("install", str(CACHE_DIR / Path(APK_URL).name)), "adb install")

    # Stage 3: device-owner grant (the unverified XOS prerequisite)
    p = _adb("shell", "dpm", "set-device-owner", RECEIVER)
    if p.returncode != 0:
        log_error(
            f"dpm set-device-owner refused: {(p.stderr or p.stdout or '').strip()}"
        )
        log_error(
            "QR fallback per DG-6: enroll via the setup wizard with BRIEF "
            "INTERNET Wi-Fi (GMS initialization needs it; Android 11 has no "
            "PROVISIONING_ALLOW_OFFLINE). Generate the QR in the web console."
        )
        _result("PARTIAL", False, "dpm refused — QR fallback indicated")
        return 3
    log_ok("device-owner granted")

    # Stage 4: confirm DO before anything that could cut ADB off
    p = _adb("shell", "dpm", "list", "owners")
    if AGENT_PKG not in (p.stdout or ""):
        raise RuntimeError(
            f"device owner not confirmed after grant: {(p.stdout or '').strip()}"
        )
    log_ok(f"device owner confirmed: {RECEIVER}")

    # Stage 5: init.json (generated by owner in the web console; this creates
    # the device row that the agent then registers under — intentional at C2)
    if not init_json:
        raise RuntimeError(
            "--init-json PATH required: generate init.json in the web console "
            "('Generate JSON' on the configuration's QR icon) first"
        )
    init_path = Path(init_json)
    if not init_path.is_file():
        raise RuntimeError(f"init.json not found: {init_path}")
    try:
        content = init_path.read_text()
        parsed = json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"init.json unreadable/invalid: {e}") from e
    if domain not in content:
        raise RuntimeError(
            f"init.json does not reference {domain} — wrong configuration?"
        )
    log_ok(
        f"init.json valid ({len(parsed) if isinstance(parsed, dict) else 'n/a'} top-level keys)"
    )
    _shell_out(_adb("push", str(init_path), INIT_JSON_ON_DEVICE), "adb push init.json")

    # Stage 6-7: launch agent + appops grants
    _shell_out(_adb("shell", "am", "start", AGENT_PKG), "am start")
    for op in ("SYSTEM_ALERT_WINDOW", "GET_USAGE_STATS"):
        _shell_out(
            _adb("shell", "appops", "set", AGENT_PKG, op, "allow"), f"appops {op}"
        )
    log_ok("agent started; appops granted (SYSTEM_ALERT_WINDOW, GET_USAGE_STATS)")

    # Stage 8: post-enrollment lockdown — ONLY after confirmed DO; ADB dies here
    if not _confirm(
        "Apply post-enrollment lockdown? This sets adb_enabled=0 and "
        "PERMANENTLY ends ADB access (recovery = factory reset, D10)"
    ):
        log_warn("lockdown skipped — ADB stays enabled; re-run --enroll to apply")
        _result("OK", True, "enrolled; lockdown deferred by owner")
        return 0
    _shell_out(
        _adb("shell", "settings", "put", "global", "development_settings_enabled", "0"),
        "development_settings_enabled=0",
    )
    _shell_out(
        _adb("shell", "settings", "put", "global", "adb_enabled", "0"),
        "adb_enabled=0",
    )
    log_ok("post-enrollment lockdown applied; ADB is now off")
    _result("OK", True, "enrolled + locked down")
    return 0


# ── --capture: §3.6 profile capture (phone-side only) ───────────────────────
def capture_steps(outdir: Path) -> list[tuple[str, str, list[list[str]]]]:
    """(description, filename, argv-list) — declarative, so --dry-run prints it."""
    bug = str(outdir / "bugreport.zip")
    return [
        ("full getprop dump", "props-full.txt", [["adb", "shell", "getprop"]]),
        (
            "Go/No-Go provisioning predicates",
            "go-no-go.txt",
            [
                ["adb", "shell", "getprop", "ro.build.fingerprint"],
                ["adb", "shell", "getprop", "ro.boot.flash.locked"],
                ["adb", "shell", "getprop", "ro.boot.verifiedbootstate"],
                ["adb", "shell", "settings", "get", "secure", "user_setup_complete"],
                ["adb", "shell", "settings", "get", "global", "device_provisioned"],
                ["adb", "shell", "settings", "get", "global", "oem_unlock_allowed"],
            ],
        ),
        (
            "device policy dump",
            "dumpsys-device-policy.txt",
            [["adb", "shell", "dumpsys", "device_policy"]],
        ),
        (
            "battery dump",
            "dumpsys-battery.txt",
            [["adb", "shell", "dumpsys", "battery"]],
        ),
        (
            "all packages + APK paths",
            "packages-f.txt",
            [["adb", "shell", "pm", "list", "packages", "-f"]],
        ),
        (
            "system packages",
            "packages-system.txt",
            [["adb", "shell", "pm", "list", "packages", "-s"]],
        ),
        (
            "third-party packages",
            "packages-3rdparty.txt",
            [["adb", "shell", "pm", "list", "packages", "-3"]],
        ),
        (
            "device features",
            "features.txt",
            [["adb", "shell", "pm", "list", "features"]],
        ),
        (
            "shared libraries",
            "libraries.txt",
            [["adb", "shell", "pm", "list", "libraries"]],
        ),
        ("long-form bugreport", "bugreport.zip", [["adb", "bugreport", bug]]),
    ]


def run_capture(dry_run: bool) -> int:
    outdir = STATE_DIR / datetime.now(timezone.utc).strftime("profile-%Y%m%d-%H%M")
    steps = capture_steps(outdir)
    if dry_run:
        log_step(f"capture DRY-RUN — would write to {outdir} (no device needed)")
        for desc, fname, cmds in steps:
            for argv in cmds:
                log(f"  {' '.join(argv)}")
            log(f"    -> {fname}  ({desc})")
        _result("OK", False, "capture dry-run printed")
        return 0

    _one_device()
    outdir.mkdir(parents=True, exist_ok=True)
    log_step(f"capturing §3.6 profile into {outdir}")

    manifest: dict = {
        "profile_schema_version": 1,
        "capture_date": datetime.now(timezone.utc).isoformat(),
        "join_key": "ro.build.fingerprint (never serial, D10)",
    }
    rc_records: list[dict] = []

    # targeted props first (manifest needs the fingerprint)
    p = _adb("shell", "getprop", "ro.build.fingerprint")
    manifest["build_fingerprint"] = (p.stdout or "").strip()
    p = _adb("version")
    m = re.search(r"Android Debug Bridge version ([\d.]+)", p.stdout or "")
    manifest["platform_tools_version"] = m.group(1) if m else "unknown"

    for desc, fname, cmds in steps:
        if fname.endswith(".txt"):
            parts: list[str] = []
            for argv in cmds:
                try:
                    q = subprocess.run(
                        argv, capture_output=True, text=True, check=False, timeout=120
                    )
                    rc = q.returncode
                    out = (q.stdout or "").rstrip()
                except subprocess.TimeoutExpired:
                    rc, out = 124, ""
                parts.append(f"$ {' '.join(argv[2:])}\n{out}\n")
                rc_records.append({"file": fname, "cmd": " ".join(argv[2:]), "rc": rc})
            (outdir / fname).write_text("\n".join(parts))
        else:  # bugreport
            try:
                q = _adb("bugreport", str(outdir / fname), timeout=900)
                rc = q.returncode
            except subprocess.TimeoutExpired:
                rc = 124
            rc_records.append({"file": fname, "cmd": "adb bugreport", "rc": rc})
        log(f"  {fname:28s} {desc}")

    manifest["commands"] = rc_records
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    bad = [r for r in rc_records if r["rc"] != 0]
    if bad:
        log_warn(f"non-zero command rc(s): {bad}")
        _result("PARTIAL", True, f"captured with {len(bad)} failing command(s)")
        return 3
    log_ok(f"profile captured: {outdir} (fingerprint join key, never serial)")
    _result("OK", True, f"captured {outdir.name}")
    return 0


# ── main ─────────────────────────────────────────────────────────────────────
USAGE = """Usage: python3 lib/67-mdm-provision.py [--verify|--enroll [--init-json PATH]|--capture [--dry-run]]
    (default run)   stage agent APK + verify 443 chain; never touches a phone
    --verify        convergence check only (needs a cached sudo ticket)
    --enroll        guided ADB device-owner enrollment (interactive, C2)
    --capture       §3.6 profile capture into ~/.local/state/mdm/profile-<ts>/
"""


def main(argv: list[str]) -> int:
    args = argv[1:]
    if {"-h", "--help"} & set(args):
        print(USAGE)
        return 0

    if not require_user("lib/67-mdm-provision.py", "python3 lib/67-mdm-provision.py"):
        return 1

    if is_vm():
        log_step("67-mdm-provision: running in VM, skipping")
        _result("SKIP", False, "in VM")
        return 2

    try:
        if "--enroll" in args:
            init_json = None
            if "--init-json" in args:
                init_json = args[args.index("--init-json") + 1]
            return run_enroll(init_json)

        if "--capture" in args:
            return run_capture(dry_run="--dry-run" in args)

        if "--verify" in args:
            if _run_priv("true")[0] != 0:
                log_error(
                    "no cached sudo ticket — run `sudo -v` (or ./install.py --only 67) "
                    "in your terminal, then retry --verify"
                )
                _result("FAIL", False, "sudo unavailable for --verify")
                return 1
            ok, msgs = converged()
            for m in msgs:
                log(f"  {m}")
            return 0 if ok else 3

        # Default trunk run: laptop-side only, idempotent, phone-free.
        verify_prereqs()
        changed = stage_apk()
        ok, msgs = converged()
        for m in msgs:
            log(f"  {m}")
        if ok:
            _result("OK", changed, "mdm-provision converged")
            return 0
        log_warn("convergence check failed")
        _result("FAIL", changed, "post-staging verification failed")
        return 1

    except Exception as exc:
        log_error(f"67-mdm-provision: {exc}")
        _result("FAIL", False, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
