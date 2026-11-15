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

Server-side flows also live behind explicit flags (the default trunk
run never touches a phone or the server):
  --enroll           guided ADB device-owner enrollment (C2, interactive).
                     Read-only vs the server (spec §3.5, A7 end-to-end):
                     the provisioning payload is derived live from
                     config.txt + the public endpoints (row probe + QR
                     JSON) and written to ~/.cache/mdm/init.json — never
                     prompts for the admin password, never creates rows
                     (errors name --ensure-device).
  --capture [--dry-run]
                     post-enrollment profile capture (context.md §3.6)
  --ensure-device [--admin-login NAME] [--admin-password-file PATH]
                     guarantee the server-side device row exists (spec
                     §3.5 A7) and (re)generate init.json for it — the
                     only server-mutating, password-asking flow.
                     Credential-free when the row already exists and a
                     cached init.json carries the configuration key.

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
    3  partial (dpm refusal → QR fallback indicated / device row unconfirmed → lockdown refused / not converged)
Stdlib only.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# Polling gate for server-side device row confirmation (enrollment proof)
POLL_INTERVAL_S = 5  # agent retries every 15s internally; 5s is fine-grained enough
POLL_TIMEOUT_S = 120  # ~8 agent retry cycles before declaring unverified


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


def device_row_state(domain: str, device_id: str) -> tuple[bool, str]:
    """One probe of the public sync endpoint: is the device registered?

    Answers 200 with error.notfound.device until the agent's first sync
    registers the device (observed live 2026-11-12); any other JSON body
    counts as registered. Non-JSON / failed requests count as not-yet.
    """
    url = f"https://{domain}/rest/public/sync/configuration/{device_id}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read(65536).decode("utf-8", "replace")
    except Exception as e:
        return False, f"request failed: {e}"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False, f"non-JSON body ({len(body)} B)"
    if parsed.get("message") == "error.notfound.device":
        return False, "error.notfound.device"
    return True, f"registered (status={parsed.get('status')!r}, {len(body)} B)"


# ── --ensure-device: server-side row + init.json (spec §3.5, A7) ────────────
# Verified against hmdm-7.04.1-os.war (static analysis + live probes,
# 2026-11-14): JWT login accepts Bearer on /rest/private/*; PUT devices with
# id=null inserts and refuses DEVICE_EXISTS on a taken number (admin path is
# independent of the server's createNewDevices flag, which gates public
# self-registration only); the public qr/json endpoint serves the console's
# JSON-download equivalent with an EMPTY DEVICE_ID placeholder.
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{4,64}$")
HTTP_TIMEOUT_S = 15
INIT_JSON_PATH = CACHE_DIR / "init.json"


def load_device_vars() -> tuple[str, str]:
    """MDM_DEVICE_ID + MDM_DEVICE_CONFIG from config.txt (same rule as
    load_domain: missing key → error naming the key; load_env itself
    already fails on an empty value, line-numbered)."""
    from install import load_env  # noqa: E402  (repo-root import)

    env = load_env()
    device_id = env.get("MDM_DEVICE_ID", "").strip()
    if not device_id:
        raise RuntimeError("MDM_DEVICE_ID not set in config.txt")
    config_name = env.get("MDM_DEVICE_CONFIG", "").strip()
    if not config_name:
        raise RuntimeError("MDM_DEVICE_CONFIG not set in config.txt")
    if not DEVICE_ID_RE.match(device_id):
        raise RuntimeError(
            f"MDM_DEVICE_ID must be 4-64 chars of [A-Za-z0-9._-], got: {device_id!r}"
        )
    return device_id, config_name


def _http_json(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, Any]:
    """One JSON HTTP call → (status, parsed body). Non-JSON bodies come
    back as raw strings; every raise carries the verbatim server message."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            code, raw = resp.getcode(), resp.read(1_048_576).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            raw = e.read(1_048_576).decode("utf-8", "replace")
        except Exception:
            raw = ""
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    return code, parsed


def _server_error(code: int, parsed: Any) -> str:
    """Compact, human-readable server error — the verbatim message when the
    body is JSON, else the HTML error page reduced to its title/text
    (Tomcat dumps a ~2 KB page on 401; the operator needs one line)."""
    if isinstance(parsed, dict) and parsed.get("message"):
        return str(parsed["message"])
    if isinstance(parsed, str) and parsed:
        m = re.search(r"<title>(.*?)</title>", parsed)
        if m:
            text = f"HTTP {code}: {m.group(1).strip()}"
        else:
            text = re.sub(r"<[^>]+>", " ", parsed)
            text = " ".join(text.split())
            text = f"HTTP {code}: {text}" if text else f"HTTP {code}"
        return text[:300] + "…" if len(text) > 300 else text
    return f"HTTP {code}: {parsed!r}"


def _jwt_login(domain: str, login: str, password: str) -> str:
    """POST /rest/public/jwt/login with MD5(password) → id_token.

    U11: upstream docs say UPPERCASE MD5 but the 7.04.1 auth classes show
    no toUpperCase — lowercase first, uppercase fallback on failure.
    """
    url = f"https://{domain}/rest/public/jwt/login"
    body = {"login": login, "password": hashlib.md5(password.encode()).hexdigest()}
    for attempt in ("lowercase", "uppercase"):
        code, parsed = _http_json(url, "POST", body)
        if code == 200 and isinstance(parsed, dict) and parsed.get("id_token"):
            return str(parsed["id_token"])
        if attempt == "lowercase":
            body["password"] = body["password"].upper()
            continue
        raise RuntimeError(f"jwt login failed — {_server_error(code, parsed)}")
    raise RuntimeError("jwt login: unreachable")  # pragma: no cover


def _resolve_config(domain: str, token: str, config_name: str) -> tuple[int, str]:
    """GET /rest/private/configurations/search → (configurationId, qrCodeKey).

    Tolerates the list shape and {items: [...]}/{data: [...]}/wrapper
    shapes; fails loudly on anything else (server shape is version-pinned).
    """
    code, parsed = _http_json(
        f"https://{domain}/rest/private/configurations/search", token=token
    )
    if code != 200:
        raise RuntimeError(
            f"configurations search failed — {_server_error(code, parsed)}"
        )
    if isinstance(parsed, dict):
        items = parsed.get("items", parsed.get("data"))
    else:
        items = parsed
    if not isinstance(items, list):
        raise RuntimeError(f"configurations search: unexpected shape: {parsed!r}")
    for item in items:
        if isinstance(item, dict) and item.get("name") == config_name:
            cid, key = item.get("id"), item.get("qrCodeKey")
            if cid is None or not key:
                raise RuntimeError(
                    f"configuration {config_name!r} missing id/qrCodeKey: {item!r}"
                )
            return int(cid), str(key)
    names = [i.get("name") for i in items if isinstance(i, dict)]
    raise RuntimeError(
        f"configuration {config_name!r} not found (server returned: {names})"
    )


def _create_device(domain: str, token: str, device_id: str, config_id: int) -> None:
    """PUT /rest/private/devices {id: null, number, configurationId} → insert.

    A taken number is refused server-side (DEVICE_EXISTS) — this module
    never overwrites a row; conflicts surface verbatim as failures.
    """
    code, parsed = _http_json(
        f"https://{domain}/rest/private/devices",
        "PUT",
        body={"id": None, "number": device_id, "configurationId": config_id},
        token=token,
    )
    if not 200 <= code < 300:
        raise RuntimeError(f"device create failed — {_server_error(code, parsed)}")


def _cached_qr_key() -> str | None:
    """com.hmdm.CONFIG of an existing init.json IS the configuration's
    qrCodeKey — lets the credential-free happy path fetch the public QR
    JSON without authenticating (it is per-configuration, not per-device)."""
    try:
        parsed = json.loads(INIT_JSON_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(parsed, dict):
        key = parsed.get("com.hmdm.CONFIG")
        if isinstance(key, str) and key:
            return key
    return None


def _build_init_json(domain: str, qr_key: str, device_id: str) -> dict[str, Any]:
    """GET /rest/public/qr/json/{key} → console-equivalent enrollment JSON,
    DEVICE_ID injected. Fails if the payload does not reference our domain
    (never write an init.json that points the phone at another server)."""
    code, parsed = _http_json(f"https://{domain}/rest/public/qr/json/{qr_key}")
    if code != 200 or not isinstance(parsed, dict):
        raise RuntimeError(f"qr json fetch failed — {_server_error(code, parsed)}")
    base_url = str(parsed.get("com.hmdm.BASE_URL", ""))
    if base_url and domain not in base_url:
        raise RuntimeError(
            f"qr json BASE_URL {base_url!r} does not reference {domain} "
            "— wrong configuration or server?"
        )
    parsed["com.hmdm.DEVICE_ID"] = device_id
    # The live 7.04.1 qr/json payload omits com.hmdm.CONFIG, but the
    # console-generated init.json (the shape the agent has already
    # accepted once) carries it — inject the qrCodeKey we resolved so the
    # file matches the console shape exactly.
    parsed.setdefault("com.hmdm.CONFIG", qr_key)
    return parsed


def acquire_init_payload(domain: str) -> tuple[Path, str]:
    """Enroll's facts stage (A7 end-to-end): derive the provisioning
    payload from config.txt + the server, strictly read-only.

    1. device_id ← config.txt (only source; regex-validated)
    2. row must already exist on the public sync endpoint (never created
       here — creation is --ensure-device's gated job)
    3. qrCodeKey ← cached init.json's com.hmdm.CONFIG (the one thing the
       cache is ever consulted for; the sync/qr endpoints do not reveal
       it — live-verified 2026-11-15)
    4. payload REBUILT live from GET /rest/public/qr/json/{key} + the
       device ID — the cached content itself is never pushed (I3)
    5. guard: payload BASE_URL must reference the domain
    6. atomic rewrite of the canonical artifact → returns (path, device_id)

    Credential-free by construction — every failure names --ensure-device.
    """
    device_id, _config_name = load_device_vars()

    row_ok, detail = device_row_state(domain, device_id)
    if not row_ok:
        raise RuntimeError(
            f"device row {device_id} not on server ({detail}) — "
            "run --ensure-device first (it offers the creation gate)"
        )

    qr_key = _cached_qr_key()
    if qr_key is None:
        raise RuntimeError(
            f"no usable cached qrCodeKey in {INIT_JSON_PATH} — run "
            "--ensure-device first (its admin password is needed ONCE to "
            "resolve the configuration key; enroll never asks)"
        )

    payload = _build_init_json(domain, qr_key, device_id)
    init_path = _write_init_json(payload)
    log_ok(f"init.json acquired from server → {init_path} (device {device_id})")
    return init_path, device_id


def _write_init_json(payload: dict[str, Any]) -> Path:
    """Atomic 0644 write (no secrets in the payload; the admin password
    never comes near this path)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INIT_JSON_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(INIT_JSON_PATH)
    return INIT_JSON_PATH


def _read_admin_password(password_file: str | None) -> str:
    """getpass prompt (default) or --admin-password-file (must be 0600).
    Never logged, never echoed, never stored — config.txt stays secret-free."""
    if password_file:
        p = Path(password_file).expanduser()
        if not p.is_file():
            raise RuntimeError(f"admin password file not found: {p}")
        mode = p.stat().st_mode & 0o777
        if mode != 0o600:
            raise RuntimeError(f"admin password file must be 0600, got {mode:03o}: {p}")
        pw = p.read_text().rstrip("\n")
        if not pw:
            raise RuntimeError(f"admin password file is empty: {p}")
        return pw
    pw = getpass.getpass("MDM admin password: ")
    if not pw:
        raise RuntimeError("empty admin password")
    return pw


def run_ensure_device(admin_login: str, password_file: str | None) -> int:
    """Guarantee the device row + a fresh init.json. Happy path is
    credential-free (public sync probe + cached qrCodeKey); the create
    path sits behind an explicit y/N gate and the admin password."""
    domain = load_domain()
    device_id, config_name = load_device_vars()

    log_step(f"ensure-device: checking row {device_id} on {domain}")
    row_ok, detail = device_row_state(domain, device_id)
    created = False
    qr_key: str | None = None

    if row_ok:
        log_ok(f"device row exists ({detail})")
        qr_key = _cached_qr_key()
        if qr_key is None:
            # No cached key → authenticate once to resolve it (read-only).
            log_step("no cached qrCodeKey — authenticating to resolve it")
            token = _jwt_login(domain, admin_login, _read_admin_password(password_file))
            _, qr_key = _resolve_config(domain, token, config_name)
    else:
        log(f"  device row not found: {detail}")
        if not _confirm(
            f"Create device row '{device_id}' on {domain} (admin REST, "
            "configuration " + repr(config_name) + ")?"
        ):
            log("aborted by owner — no server mutation")
            _result("OK", False, "ensure-device aborted by owner")
            return 0
        token = _jwt_login(domain, admin_login, _read_admin_password(password_file))
        config_id, qr_key = _resolve_config(domain, token, config_name)
        try:
            _create_device(domain, token, device_id, config_id)
            created = True
        except RuntimeError as e:
            # The sync probe can be ahead of/behind the REST view; a
            # DEVICE_EXISTS refusal means the row is there — treat as
            # existing and continue (never overwrite; idempotent re-run).
            if "DEVICE_EXISTS" not in str(e):
                raise
            log_ok(f"server reports the number taken — row already exists ({e})")
        row_ok, detail = device_row_state(domain, device_id)
        if row_ok:
            log_ok(f"device row confirmed via sync endpoint ({detail})")
        else:
            # PUT returned success; the sync view may need the agent's first
            # poll to settle — the enroll flow's poll gate is the real proof.
            log_warn(f"sync endpoint not confirming yet: {detail} — continuing")

    assert qr_key is not None
    init_path = _write_init_json(_build_init_json(domain, qr_key, device_id))
    log_ok(f"init.json written: {init_path} (device {device_id})")
    verb = "created" if created else "exists"
    _result("OK", created, f"device row {verb}; init.json written for {device_id}")
    return 0


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
    """Raise when a phone-side command fails, showing its stderr/stdout.
    Also detects remote-shell failures that this adb build reports with rc=0
    (e.g., "Error: unknown command", "Exception occurred", line-start "Error:").
    """
    out = (cmd.stdout or "") + (cmd.stderr or "")
    if cmd.returncode != 0 or _looks_like_remote_error(out):
        detail = out.strip()
        raise RuntimeError(f"{what} failed (rc={cmd.returncode}): {detail}")


def _looks_like_remote_error(text: str) -> bool:
    """Check if adb shell output contains Android-side error patterns that
    don't reflect in the exit code (this build returns rc=0 on remote errors)."""
    for line in text.splitlines():
        s = line.strip()
        if (
            s.startswith("Error:")
            or s.startswith("Exception occurred")
            or "unknown command" in s
        ):
            return True
    return False


def device_owner_state() -> str | None:
    """Read device owner from dumpsys device_policy (Android 11 has no
    `dpm list owners` subcommand; confirmation must parse output text).
    Returns the component string if set, None otherwise."""
    p = _adb("shell", "dumpsys", "device_policy")
    m = re.search(
        r"Device Owner:\s*\n\s*admin=(ComponentInfo\{[^}]+\})", p.stdout or ""
    )
    return m.group(1) if m else None


# ── --enroll: guided ADB device-owner enrollment (C2, interactive) ───────────
def run_enroll() -> int:
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

    # Facts stage — ALL server-side info in hand BEFORE anything touches
    # the phone (A7 end-to-end; read-only vs the server, credential-free).
    log_step("enroll facts: derive init.json from config.txt + server (A7)")
    init_path, device_id = acquire_init_payload(domain)

    log_step("enroll preflight: device gates")
    _one_device()
    # AOSP-accurate device-owner preconditions (Android 11 DPMS):
    # adb-initiated set-device-owner is refused for an existing device/profile
    # owner, >1 user, or ANY AccountManager account — a completed setup wizard
    # is NOT a blocker (dpms: enforceCanSetDeviceOwnerLocked:13304-13305,
    # 13321-13335, hasIncompatibleAccountsOrNonAdbNoLock:14566-14574).
    p = _adb("shell", "pm", "list", "users")
    user_rows = [
        ln.strip() for ln in (p.stdout or "").splitlines() if "UserInfo{" in ln
    ]
    if len(user_rows) != 1:
        raise RuntimeError(
            f"expected exactly 1 user, found {len(user_rows)}: {user_rows} — "
            "work profiles / app-clone users must be removed before the grant"
        )
    log_ok(f"single user ({user_rows[0]})")

    p = _adb("shell", "dumpsys", "account")
    accounts = [ln.strip() for ln in (p.stdout or "").splitlines() if "Account {" in ln]
    if accounts:
        raise RuntimeError(
            f"{len(accounts)} AccountManager account(s) present — sign out of "
            "every one in Settings > Accounts; any single account blocks "
            "set-device-owner (the HMDM admin is not test-only)"
        )
    log_ok("zero accounts")

    p = _adb("shell", "getprop", "ro.build.fingerprint")
    fp = (p.stdout or "").strip()
    log(f"  device fingerprint: {fp}")
    log(
        f"  android: {_adb('shell', 'getprop', 'ro.build.version.release').stdout.strip()} "
        f"(API {_adb('shell', 'getprop', 'ro.build.version.sdk').stdout.strip()})"
    )
    p = _adb("shell", "settings", "get", "secure", "user_setup_complete")
    log(
        f"  user_setup_complete: {(p.stdout or '').strip()} (informational — "
        "not an adb-path blocker)"
    )

    # Stage 1-3: device-owner grant (idempotent — resume if already granted)
    existing_owner = device_owner_state()
    if existing_owner and "com.hmdm.launcher" in existing_owner:
        log_ok(f"device owner already set: {existing_owner} — skipping install+grant")
        if not _confirm("Continue with init.json push + agent start?"):
            log("aborted by owner")
            return 1
    else:
        # Stage 1-2: install the pinned agent APK
        if not _confirm("Install the staged agent APK now?"):
            log("aborted by owner")
            return 1
        _shell_out(_adb("install", str(CACHE_DIR / Path(APK_URL).name)), "adb install")

        # Stage 3: device-owner grant (the unverified XOS prerequisite)
        p = _adb("shell", "dpm", "set-device-owner", RECEIVER)
        if (
            _looks_like_remote_error(p.stdout or "")
            or _looks_like_remote_error(p.stderr or "")
            or p.returncode != 0
        ):
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
    confirmed_owner = device_owner_state()
    if not confirmed_owner or "com.hmdm.launcher" not in confirmed_owner:
        raise RuntimeError(
            f"device owner not confirmed after grant: {confirmed_owner or 'none'}"
        )
    log_ok(f"device owner confirmed: {confirmed_owner}")

    # Stage 5: init.json push — the payload was already derived and written
    # by the facts stage (acquire_init_payload); push it to the device-side
    # path the agent reads (AdminReceiver.updateSettingsFromFile — the ONLY
    # provisioning source on the ADB path).
    _shell_out(_adb("push", str(init_path), INIT_JSON_ON_DEVICE), "adb push init.json")

    # Stage 6-7: launch agent + appops grants
    _shell_out(_adb("shell", "am", "start", AGENT_PKG), "am start")
    for op in ("SYSTEM_ALERT_WINDOW", "GET_USAGE_STATS"):
        _shell_out(
            _adb("shell", "appops", "set", AGENT_PKG, op, "allow"), f"appops {op}"
        )
    log_ok("agent started; appops granted (SYSTEM_ALERT_WINDOW, GET_USAGE_STATS)")

    # Stage 8: server-side device row — the pipeline gate. Lockdown is
    # offered ONLY once the server knows the device (the missed gate of the
    # 2026-11-12 attempt: lockdown fired with the row never confirmed).
    # Honesty: the row may pre-exist (--ensure-device created it), so a
    # green poll proves the pipeline, NOT that the phone itself synced —
    # row_pre_existed feeds the note after the loop.
    row_pre_existed, _detail = device_row_state(domain, device_id)
    log_step(f"polling server for device row {device_id} (pipeline gate)")
    attempts = POLL_TIMEOUT_S // POLL_INTERVAL_S
    ok, detail = False, ""
    for n in range(1, attempts + 1):
        ok, detail = device_row_state(domain, device_id)
        if ok:
            break
        log(f"  poll {n}/{attempts}: {detail}")
        time.sleep(POLL_INTERVAL_S)
    if not ok:
        log_error(
            f"device row NOT confirmed after {POLL_TIMEOUT_S}s (last: {detail}) "
            "— lockdown REFUSED, ADB stays enabled. Check the agent on the "
            "phone (XOS battery manager may kill it), then re-run --enroll "
            "(idempotent) once it registers. Row pre-exists server-side, so "
            "an agent that never syncs is the suspect, not the row."
        )
        _result(
            "PARTIAL", True, "enrollment unverified — lockdown refused, ADB preserved"
        )
        return 3
    log_ok(f"device row confirmed on server: {device_id}")
    if row_pre_existed:
        log(
            "  note: row existed before the agent started — a green poll is a "
            "pipeline check, NOT proof the phone synced. Proof: console "
            "last-sync turns fresh AND the phone shows the managed launcher."
        )

    # Stage 9: post-enrollment lockdown — ONLY after server-confirmed device row; ADB dies here
    if not _confirm(
        "Apply post-enrollment lockdown? BEFORE y: verify the web console "
        "shows a FRESH last-sync on this device AND the phone runs the "
        "managed launcher (the poll gate cannot prove the phone moved — the "
        "row pre-exists). Also run --capture first if not done. This sets "
        "adb_enabled=0 and PERMANENTLY ends ADB access (recovery = factory "
        "reset, D10)"
    ):
        log_warn("lockdown skipped — ADB stays enabled; re-run --enroll to apply")
        _result("OK", True, "enrolled; lockdown deferred by owner")
        return 0
    # Both settings in ONE shell line (official HMDM ADB guidance, qa 19025):
    # each separate `adb shell` session can be severed by the settings change,
    # so the pair must ride a single device-side shell.
    _shell_out(
        _adb(
            "shell",
            "settings put global development_settings_enabled 0; "
            "settings put global adb_enabled 0",
        ),
        "post-enrollment lockdown (both settings, one shell line)",
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
    outdir = STATE_DIR / datetime.now(timezone.utc).strftime("profile-%Y%m%d-%H%M%S")
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
USAGE = """Usage: python3 lib/67-mdm-provision.py [--verify|--enroll|--capture [--dry-run]|--ensure-device [--admin-login NAME] [--admin-password-file PATH]]
    (default run)   stage agent APK + verify 443 chain; never touches a phone
    --verify        convergence check only (needs a cached sudo ticket)
    --enroll        guided ADB device-owner enrollment (interactive, C2);
                    read-only vs the server — payload derived live from
                    config.txt + public endpoints, no admin password
    --capture       §3.6 profile capture into ~/.local/state/mdm/profile-<ts>/
    --ensure-device guarantee server-side device row + init.json (§3.5 A7;
                    credential-free when the row already exists with a cached
                    key; admin password via getpass or 0600 file, never here)
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
            return run_enroll()

        if "--capture" in args:
            return run_capture(dry_run="--dry-run" in args)

        if "--ensure-device" in args:
            admin_login, password_file = "admin", None
            if "--admin-login" in args:
                admin_login = args[args.index("--admin-login") + 1]
            if "--admin-password-file" in args:
                password_file = args[args.index("--admin-password-file") + 1]
            return run_ensure_device(admin_login, password_file)

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
