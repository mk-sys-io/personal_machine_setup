#!/usr/bin/env python3
"""MDM provisioning — enrollment pre-flight (Phase 2: loopback + adb reverse).

Laptop-side provisioning for the ADB-primary device-owner enrollment (DG-6).
The canonical agent<->server channel is the phone's http://127.0.0.1:8080,
carried over USB by two `adb reverse` tunnels (D13):
  tcp:8080  - HTTP (agent sync, config fetch, app downloads)
  tcp:31000 - MQTT (push notifications)
There is no tether, no LAN, no DNS in the agent path, and no per-session
address: the base URL is a protocol constant. The forwards are ephemeral
(reboot / replug / adb kill-server); --ensure-channel re-applies them on
demand and the mdm-channel.service user watcher keeps them applied.

1. Stage the official Headwind agent APK (size + sha256 pinned; never
   recompiled - Play Protect blocks custom builds).

Server-side flows live behind explicit flags (the default trunk run never
touches a phone or the server):
  --enroll [--admin-login NAME]
                     guided ADB device-owner enrollment (C2, interactive).
                     Preflight = canonical reverse channel (both forwards
                     applied + Tomcat 200 + MQTT TCP connect). --enroll first
                     applies etc/mdm/policy/policy.json to the server
                     configuration behind a y/N prompt + the admin password
                     (getpass), then continues with the device handoff; the
                     provisioning payload is still derived live from
                     config.txt + the public endpoints and written to
                     ~/.cache/mdm/init.json with BASE_URL =
                     http://127.0.0.1:8080. On success, prints the
                     --ensure-device repair command when server-issued
                     app URLs are stale.
  --capture [--dry-run]
                     post-enrollment profile capture (context.md §3.6)
  --ensure-device [--admin-login NAME]
                     guarantee the server-side device row exists, (re)generate
                     init.json, and re-point stale application-version URLs
                     (MDM_DOMAIN hosts and private/loopback IP literals) to
                     http://127.0.0.1:8080 - one of two server-mutating,
                     password-asking flows (--enroll applies policy first).
  --ensure-channel   apply both adb reverse forwards, verify the panel and
                     the MQTT broker, and start an inactive watcher.

 --admin-login NAME is accepted by both --enroll and --ensure-device
 (default: admin).

ADB is permanent (DG-13 B1): there is no adb_enabled=0 step anywhere.

Test seams (offline harness ONLY - never runtime fallbacks): env vars
prefixed MDM_TEST_ (MDM_TEST_BASE_URL, MDM_TEST_MQTT_PORT,
MDM_TEST_SKIP_UNITS, MDM_TEST_SKIP_WATCHER) stand in for live hardware.

Run standalone: python3 lib/67-mdm-provision.py
Wired into install.py as 67-mdm-provision

Exit codes:
    0  converged
    1  failure
    2  skip (in VM)
    3  partial (device absent/unauthorized, forwards missing, not converged)
Stdlib only.
"""

from __future__ import annotations

import getpass
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from preconditions import require_user  # noqa: E402, I001
from mdm_policy_data import anti_coupling_violations, load_policy as _shared_load_policy  # noqa: E402


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


class UsageError(RuntimeError):
    """Bad invocation: usage printed to stderr; main() maps it to exit 1."""


def _usage_error(msg: str) -> UsageError:
    rerun = " ".join(["python3", "lib/67-mdm-provision.py", *sys.argv[1:]])
    log_error(f"{msg} — rerun: {rerun}")
    print(USAGE, file=sys.stderr)
    return UsageError(msg)


def _parse_admin_login(args: list[str]) -> str:
    login = "admin"
    for index, token in enumerate(args):
        if token == "--admin-login":
            if index + 1 >= len(args) or args[index + 1].startswith("-"):
                raise _usage_error("--admin-login requires a NAME")
            login = args[index + 1]
        elif token.startswith("--admin-login="):
            login = token.split("=", 1)[1]
            if not login:
                raise _usage_error("--admin-login requires a NAME")
    return login


_ALLOWED_FLAGS = {
    "-h",
    "--help",
    "--verify",
    "--enroll",
    "--capture",
    "--dry-run",
    "--ensure-device",
    "--ensure-channel",
    "--admin-login",
    "--watch-channel",
}


def _validate_args(args: list[str]) -> None:
    for token in args:
        if token.startswith("--admin-login="):
            continue
        if token.startswith("-") and token not in _ALLOWED_FLAGS:
            raise _usage_error(f"unknown flag: {token}")


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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── canonical reverse channel (D13): phone loopback over USB ────────────────
# The agent talks to http://127.0.0.1:8080 on the phone; `adb reverse` carries
# it over USB. The URL is a protocol constant — no address is derived or
# configured. The tunnels are ephemeral (reboot, replug, adb kill-server) and
# are re-applied by --ensure-channel / the mdm-channel.service watcher.
TEST_ENV_PREFIX = "MDM_TEST_"
SERVER_PORT = 8080
LOOPBACK = "127.0.0.1"
MQTT_PORT = 31000
REVERSE_PORTS = (SERVER_PORT, MQTT_PORT)
WATCH_INTERVAL_S = 5


def _test_env(name: str) -> str | None:
    """Test-only seam for the offline harness (/tmp/opencode/mdm-reverse-test):
    an MDM_TEST_* env var stands in for live hardware. Not a runtime
    fallback — with the seam unset the canonical path is used.
    """
    if not name.startswith(TEST_ENV_PREFIX):
        raise RuntimeError(f"test seams must be prefixed {TEST_ENV_PREFIX}: {name}")
    return os.environ.get(name) or None


def agent_url() -> str:
    """The canonical agent<->server base URL: the phone's loopback, carried by
    `adb reverse` (D13). Plain HTTP + IP: no DNS, no tether, no LAN."""
    seam = _test_env("MDM_TEST_BASE_URL")
    if seam:
        return seam.rstrip("/")
    return f"http://{LOOPBACK}:{SERVER_PORT}"


def _panel_probe_http(base_url: str) -> tuple[bool, str]:
    """Canonical chain step 3: Tomcat must serve the MDM panel through the
    reverse tunnel (plain HTTP + loopback — no DNS in the agent path)."""
    try:
        with urllib.request.urlopen(f"{base_url}/", timeout=10) as resp:
            code = resp.getcode()
            body = resp.read(65536).decode("utf-8", "replace")
    except Exception as e:
        return False, f"{base_url} not reachable: {e}"
    if code not in (200, 302):
        return False, f"{base_url} returned {code}"
    if "MDM web panel" not in body:
        return False, f"{base_url} does not serve the MDM web panel"
    return True, f"{base_url} serves the MDM web panel ({code})"


def _mqtt_port() -> int:
    seam = _test_env("MDM_TEST_MQTT_PORT")
    return int(seam) if seam else MQTT_PORT


def _mqtt_probe() -> tuple[bool, str]:
    """The MQTT broker must accept a TCP connection on 127.0.0.1:31000.
    A connect proves the tunnel; no MQTT handshake is sent."""
    port = _mqtt_port()
    try:
        with socket.create_connection((LOOPBACK, port), timeout=3):
            pass
    except OSError as e:
        return False, f"MQTT broker not reachable on {LOOPBACK}:{port}: {e}"
    return True, f"MQTT broker reachable on {LOOPBACK}:{port} (TCP connect)"


def _reverse_ports(serial: str) -> set[int]:
    """Remote ports currently listed by `adb -s <serial> reverse --list`.
    Tolerates both 'tcp:8080 tcp:8080' and '<serial> tcp:8080 tcp:8080'."""
    p = _adb("reverse", "--list", serial=serial)
    ports: set[int] = set()
    for line in (p.stdout or "").splitlines():
        m = re.search(r"tcp:(\d+)", line)
        if m:
            ports.add(int(m.group(1)))
    return ports


def ensure_forwards() -> tuple[str, list[int], list[str]]:
    """Apply the two canonical reverse forwards if missing.

    Returns (state, applied_ports, messages); state is one of
    ok | absent | unauthorized | multiple | error. Expected device states
    never raise — callers map them to messages and exit codes.
    """
    rows = _adb_devices()
    if not rows:
        return "absent", [], ["no device attached — connect the phone over USB"]
    if len(rows) > 1:
        return (
            "multiple",
            [],
            [f"multiple devices attached: {rows} — attach exactly one"],
        )
    serial, dev_state = rows[0]
    if dev_state == "unauthorized":
        return (
            "unauthorized",
            [],
            [
                "device unauthorized — unlock the phone and accept the RSA debugging prompt"
            ],
        )
    if dev_state != "device":
        return "error", [], [f"device {serial} is in state {dev_state!r}, not 'device'"]

    have = _reverse_ports(serial)
    applied: list[int] = []
    for port in REVERSE_PORTS:
        if port in have:
            continue
        p = _adb("reverse", f"tcp:{port}", f"tcp:{port}", serial=serial)
        if p.returncode != 0:
            return (
                "error",
                applied,
                [
                    f"adb reverse tcp:{port} failed (rc={p.returncode}): "
                    f"{(p.stderr or p.stdout or '').strip()}"
                ],
            )
        applied.append(port)

    have = _reverse_ports(serial)
    missing = [port for port in REVERSE_PORTS if port not in have]
    if missing:
        return (
            "error",
            applied,
            [f"reverse forwards missing after apply: {missing} (device {serial})"],
        )
    msgs: list[str] = []
    if applied:
        msgs.append(
            "applied reverse forwards: " + ", ".join(f"tcp:{port}" for port in applied)
        )
    msgs.append(
        f"reverse forwards active on {serial}: "
        + ", ".join(f"tcp:{port}" for port in REVERSE_PORTS)
    )
    return "ok", applied, msgs


def _forwards_state() -> tuple[str, list[str]]:
    """Read-only reverse-forward check — never mutates the phone."""
    rows = _adb_devices()
    if not rows:
        return "absent", ["no device attached — forwards not checked"]
    if len(rows) > 1:
        return "multiple", [f"multiple devices attached: {rows} — attach exactly one"]
    serial, dev_state = rows[0]
    if dev_state == "unauthorized":
        return "unauthorized", [
            "device unauthorized — unlock the phone and accept the RSA debugging prompt"
        ]
    if dev_state != "device":
        return "error", [f"device {serial} is in state {dev_state!r}, not 'device'"]
    have = _reverse_ports(serial)
    missing = [port for port in REVERSE_PORTS if port not in have]
    if missing:
        return "partial", [
            "reverse forwards missing: "
            + ", ".join(f"tcp:{port}" for port in missing)
            + " — run --ensure-channel"
        ]
    return "ok", [
        f"reverse forwards active on {serial}: "
        + ", ".join(f"tcp:{port}" for port in REVERSE_PORTS)
    ]


def channel_chain(apply: bool = False) -> tuple[str, list[str]]:
    """Canonical channel check -> (state, messages).

    The laptop side (panel, MQTT) is always probed; the device side is
    checked only when a phone is attached. `apply=True` re-applies missing
    forwards first. Expected device states never raise.
    """
    if apply:
        state, _applied, msgs = ensure_forwards()
    else:
        state, msgs = _forwards_state()

    panel_ok, panel_msg = _panel_probe_http(agent_url())
    msgs.append(panel_msg)
    mqtt_ok, mqtt_msg = _mqtt_probe()
    msgs.append(mqtt_msg)
    if not panel_ok or not mqtt_ok:
        return "error", msgs
    return state, msgs


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


# ── runtime facts that actually gate enrollment (not full --verify) ─────────


def units_active() -> tuple[bool, str]:
    """tomcat10 + postgresql must be ACTIVE now (enabled-at-boot is not enough)."""
    if _test_env("MDM_TEST_SKIP_UNITS") == "1":
        return True, "units skipped (MDM_TEST_SKIP_UNITS)"
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


def device_row_state(base_url: str, device_id: str) -> tuple[bool, str]:
    """One probe of the public sync endpoint: is the device registered?

    Answers 200 with error.notfound.device until the agent's first sync
    registers the device (observed live 2026-11-12); any other JSON body
    counts as registered. Non-JSON / failed requests count as not-yet.
    """
    url = f"{base_url}/rest/public/sync/configuration/{device_id}"
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
    """MDM_DEVICE_ID + MDM_DEVICE_CONFIG from config.txt (missing key →
    error naming the key; load_env itself already fails on an empty value,
    line-numbered)."""
    sys.path.insert(0, str(REPO_ROOT))  # repo-root import (was load_domain's)
    from install import load_env  # noqa: E402

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
    return f"HTTP {code}: {_shape_note(parsed)}"


def _envelope_data(parsed: Any) -> Any:
    """Unwrap the hmdm REST envelope {status, message, data}.

    Every endpoint declared to return com.hmdm.rest.json.Response carries
    `data` (possibly null); the flat endpoints (jwt login, qr/json) never
    do, so a non-envelope body is returned unchanged. An ERROR status is
    surfaced verbatim even when `data` is absent (minimal/malformed envelope).
    """
    if isinstance(parsed, dict) and parsed.get("status") == "ERROR":
        message = parsed.get("message")
        detail = str(message)[:200] if message else _shape_note(parsed)
        raise RuntimeError(f"Headwind API error: {detail}")
    if (
        isinstance(parsed, dict)
        and "data" in parsed
        and parsed.get("status") in ("OK", "WARNING")
    ):
        return parsed["data"]
    return parsed


def _shape_note(parsed: Any) -> str:
    """Bounded description of an unexpected body — never the body itself."""
    if isinstance(parsed, dict):
        keys = sorted(str(k) for k in parsed)
        shown = ", ".join(keys[:8]) + (", …" if len(keys) > 8 else "")
        note = f"dict with keys [{shown}]"
    elif isinstance(parsed, list):
        note = f"list of {len(parsed)}"
    else:
        note = f"{type(parsed).__name__}: {repr(parsed)[:80]}"
    return note[:240]


def _jwt_login(base_url: str, login: str, password: str) -> str:
    """POST /rest/public/jwt/login with MD5(password) → id_token.

    U11: upstream docs say UPPERCASE MD5 but the 7.04.1 auth classes show
    no toUpperCase — lowercase first, uppercase fallback on failure.
    """
    url = f"{base_url}/rest/public/jwt/login"
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


def _resolve_config(base_url: str, token: str, config_name: str) -> tuple[int, str]:
    """GET /rest/private/configurations/search → (configurationId, qrCodeKey).

    Unwraps the com.hmdm.rest.json.Response envelope; fails loudly on
    anything that is not a list (server shape is version-pinned).
    """
    code, parsed = _http_json(
        f"{base_url}/rest/private/configurations/search", token=token
    )
    if code != 200:
        raise RuntimeError(
            f"configurations search failed — {_server_error(code, parsed)}"
        )
    items = _envelope_data(parsed)
    if not isinstance(items, list):
        raise RuntimeError(
            f"configurations search: unexpected shape: {_shape_note(parsed)}"
        )
    for item in items:
        if isinstance(item, dict) and item.get("name") == config_name:
            cid, key = item.get("id"), item.get("qrCodeKey")
            if cid is None or not key:
                raise RuntimeError(
                    f"configuration {config_name!r} missing id/qrCodeKey: {_shape_note(item)}"
                )
            return int(cid), str(key)
    names = [i.get("name") for i in items if isinstance(i, dict)]
    shown = ", ".join(str(name) for name in names[:10]) + (
        ", …" if len(names) > 10 else ""
    )
    raise RuntimeError(
        f"configuration {config_name!r} not found (server returned: {shown})"
    )


def _create_device(base_url: str, token: str, device_id: str, config_id: int) -> None:
    """PUT /rest/private/devices {id: null, number, configurationId} → insert.

    A taken number is refused server-side (DEVICE_EXISTS) — this module
    never overwrites a row; conflicts surface verbatim as failures.
    """
    code, parsed = _http_json(
        f"{base_url}/rest/private/devices",
        "PUT",
        body={"id": None, "number": device_id, "configurationId": config_id},
        token=token,
    )
    if not 200 <= code < 300:
        raise RuntimeError(f"device create failed — {_server_error(code, parsed)}")
    # A 200 can still carry a success-envelope ERROR (e.g. DEVICE_EXISTS);
    # surface the server's verbatim message rather than treating it as success.
    _envelope_data(parsed)


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


def _build_init_json(base_url: str, qr_key: str, device_id: str) -> dict[str, Any]:
    """GET /rest/public/qr/json/{key} → console-equivalent enrollment JSON,
    DEVICE_ID injected and com.hmdm.BASE_URL OVERRIDDEN with the canonical
    loopback URL. The console configuration still carries the retired
    https://domain value; under D13 the payload must point the agent at
    http://127.0.0.1:8080 — the address adb reverse carries to this
    server."""
    code, parsed = _http_json(f"{base_url}/rest/public/qr/json/{qr_key}")
    if code != 200 or not isinstance(parsed, dict):
        raise RuntimeError(f"qr json fetch failed — {_server_error(code, parsed)}")
    served = str(parsed.get("com.hmdm.BASE_URL", ""))
    parsed["com.hmdm.BASE_URL"] = base_url
    # The live 7.04.1 qr/json payload omits com.hmdm.CONFIG, but the
    # console-generated init.json (the shape the agent has already
    # accepted once) carries it — inject the qrCodeKey we resolved so the
    # file matches the console shape exactly.
    parsed.setdefault("com.hmdm.CONFIG", qr_key)
    parsed["com.hmdm.DEVICE_ID"] = device_id
    if served and served != base_url:
        log(f"  BASE_URL overridden for the reverse channel: {served} → {base_url}")
    return parsed


# ── app URLs follow the canonical channel (D13 invariant) ────────────────────
# Every server-issued URL the device consumes must point at the phone's
# loopback, convergent via --ensure-device. Seed applications are provisioned
# by the console with old tether IPs or the retired MDM_DOMAIN; both classes
# are dead on the device. Stale URLs are detected credential-free from the
# public sync JSON (data.applications[].url) and rewritten via the
# application-version REST resource, verified against hmdm-7.04.1-os.war:
# GET rest/private/applications (+/{id}/versions); PUT
# rest/private/applications/versions updates when the body carries id
# (console bundle: "Create ... if id is not provided or update existing
# one otherwise"; domain class com/hmdm/persistence/domain/ApplicationVersion).
def _mdm_domain() -> str:
    """MDM_DOMAIN from config.txt — the retired-domain marker that decides
    which server-issued URLs are rewrite candidates (§8: the domain path is
    retired; a URL still pointing at it is stale by construction)."""
    sys.path.insert(0, str(REPO_ROOT))  # repo-root import (as load_device_vars)
    from install import load_env  # noqa: E402

    domain = load_env().get("MDM_DOMAIN", "").strip()
    if not domain:
        raise RuntimeError("MDM_DOMAIN not set in config.txt")
    return domain


def _is_private_host(host: str) -> bool:
    """True for IP literals that are loopback/private/link-local/unspecified —
    the dead tether-IP class. The only canonical host is 127.0.0.1 (D13)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified


def _stale_url_hint(base_url: str, device_id: str) -> str | None:
    """Non-fatal enroll success-path hint: the repair command when
    server-issued app URLs are stale, None when current (or the
    credential-free check fails — enroll must not regress on it)."""
    try:
        stale, _foreign = _stale_app_urls(base_url, device_id)
    except RuntimeError:
        return None
    if not stale:
        return None
    return (
        f"next: python3 lib/67-mdm-provision.py --ensure-device "
        f"(rewrites {len(stale)} stale app URL(s) to the canonical channel)"
    )


def _stale_app_urls(
    base_url: str, device_id: str
) -> tuple[list[dict[str, str]], list[str]]:
    """Credential-free staleness detection over the public sync JSON.

    Returns (stale, foreign):
      stale   — {pkg, version, url} entries whose netloc differs from the
                canonical loopback netloc AND whose host is MDM_DOMAIN or a
                private/loopback/link-local IP literal (rewrite candidates);
      foreign — URLs never rewritten, warned about instead: vendor-host
                APKs (h-mdm.com — they'd need re-uploading to install
                offline; rewriting would just 404 differently) and
                non-HTTP schemes (file://, market://).
    Raises when the sync JSON itself is unusable — callers already proved
    the device row, so absence of the payload is an error, not a state.
    """
    code, parsed = _http_json(f"{base_url}/rest/public/sync/configuration/{device_id}")
    if code != 200 or not isinstance(parsed, dict):
        raise RuntimeError(f"sync JSON fetch failed — {_server_error(code, parsed)}")
    if parsed.get("message") == "error.notfound.device":
        raise RuntimeError("error.notfound.device (device row missing)")
    data = _envelope_data(parsed)
    apps = data.get("applications") if isinstance(data, dict) else None
    if not isinstance(apps, list):
        raise RuntimeError(
            f"sync JSON: no applications list (data is {type(data).__name__})"
        )

    domain = _mdm_domain()
    live = urlparse(base_url)
    stale: list[dict[str, str]] = []
    foreign: list[str] = []
    for app in apps:
        if not isinstance(app, dict):
            continue
        url = app.get("url")
        if not url:
            continue
        parts = urlparse(str(url))
        if parts.scheme not in ("http", "https") or parts.hostname is None:
            foreign.append(str(url))
            continue
        if parts.netloc == live.netloc:
            continue
        if parts.hostname == domain or _is_private_host(parts.hostname):
            stale.append(
                {
                    "pkg": str(app.get("pkg") or "?"),
                    "version": str(app.get("version") or "?"),
                    "url": str(url),
                }
            )
        else:
            foreign.append(str(url))
    return stale, foreign


def _retarget_url(old_url: str, base_url: str) -> str:
    """Path-preserving re-point: swap scheme/host/port for the canonical
    loopback URL, keep everything after the netloc byte-for-byte."""
    old, base = urlparse(old_url), urlparse(base_url)
    return urlunparse(
        (base.scheme, base.netloc, old.path, old.params, old.query, old.fragment)
    )


def _repoint_app_urls(base_url: str, token: str, stale: list[dict[str, str]]) -> int:
    """PUT each stale application version with its url re-pointed to the
    canonical loopback URL (path preserved). The version object is fetched fresh
    and echoed back whole — only `url` is touched; the caller verifies the
    re-point by re-running _stale_app_urls. Fails loudly (verbatim server
    message) on any shape it cannot match — never skips silently."""
    code, parsed = _http_json(
        f"{base_url}/rest/private/applications/search", token=token
    )
    if code != 200:
        raise RuntimeError(f"applications list failed — {_server_error(code, parsed)}")
    apps = _envelope_data(parsed)
    if not isinstance(apps, list):
        raise RuntimeError(
            f"applications list: unexpected shape: {_shape_note(parsed)}"
        )

    rewritten = 0
    for entry in stale:
        pkg = entry["pkg"]
        application = next(
            (a for a in apps if isinstance(a, dict) and a.get("pkg") == pkg), None
        )
        if application is None:
            names = sorted(
                str(a.get("pkg")) for a in apps if isinstance(a, dict) and a.get("pkg")
            )
            shown = ", ".join(names[:10]) + ("…" if len(names) > 10 else "")
            raise RuntimeError(
                f"application {pkg!r} not found on server (list had: {shown})"
            )
        code, parsed = _http_json(
            f"{base_url}/rest/private/applications/{application['id']}/versions",
            token=token,
        )
        if code != 200:
            raise RuntimeError(
                f"versions for {pkg} failed — {_server_error(code, parsed)}"
            )
        versions = _envelope_data(parsed)
        if not isinstance(versions, list):
            raise RuntimeError(
                f"versions for {pkg}: unexpected shape: {_shape_note(parsed)}"
            )
        version = next(
            (
                v
                for v in versions
                if isinstance(v, dict) and v.get("url") == entry["url"]
            ),
            None,
        )
        if version is None:
            urls = sorted(str(v.get("url")) for v in versions if isinstance(v, dict))
            shown = ", ".join(urls[:10]) + ("…" if len(urls) > 10 else "")
            raise RuntimeError(
                f"version with url {entry['url']!r} not found under {pkg} "
                f"(have: {shown})"
            )
        version["url"] = _retarget_url(entry["url"], base_url)
        code, parsed = _http_json(
            f"{base_url}/rest/private/applications/versions",
            "PUT",
            body=version,
            token=token,
        )
        if not 200 <= code < 300:
            raise RuntimeError(
                f"app version PUT failed for {pkg} — {_server_error(code, parsed)}"
            )
        _envelope_data(parsed)  # HTTP 200 can still hide an envelope ERROR
        log(f"  {pkg} {entry['version']}: {entry['url']} → {version['url']}")
        rewritten += 1
    return rewritten


def acquire_init_payload() -> tuple[Path, str]:
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
    5. guard: payload BASE_URL = http://127.0.0.1:8080 (overridden —
       never write an init.json that points the phone anywhere else)
    6. atomic rewrite of the canonical artifact → returns (path, device_id)

    Credential-free by construction (policy sync is a separate, earlier stage) — every failure names --ensure-device.
    """
    device_id, _config_name = load_device_vars()
    base_url = agent_url()

    row_ok, detail = device_row_state(base_url, device_id)
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

    payload = _build_init_json(base_url, qr_key, device_id)
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


def _read_admin_password() -> str:
    """getpass prompt; the only password source. Never logged, never echoed,
    never stored — config.txt stays secret-free."""
    password = _test_env("MDM_TEST_ADMIN_PASSWORD")
    if password:
        return password
    pw = getpass.getpass("MDM admin password: ")
    if not pw:
        raise RuntimeError("empty admin password")
    return pw


def run_ensure_device(admin_login: str) -> int:
    """Guarantee the device row + a fresh init.json. Happy path is
    credential-free (public sync probe + cached qrCodeKey); the create
    path sits behind an explicit y/N gate and the admin password."""
    base_url = agent_url()
    device_id, config_name = load_device_vars()

    log_step(f"ensure-device: checking row {device_id} on {base_url}")
    row_ok, detail = device_row_state(base_url, device_id)
    created = False
    token: str | None = None
    stale: list[dict[str, str]] = []
    repointed = 0
    qr_key: str | None = None

    if row_ok:
        log_ok(f"device row exists ({detail})")
        qr_key = _cached_qr_key()
        if qr_key is None:
            # No cached key → authenticate once to resolve it (read-only).
            log_step("no cached qrCodeKey — authenticating to resolve it")
            token = _jwt_login(base_url, admin_login, _read_admin_password())
            _, qr_key = _resolve_config(base_url, token, config_name)
    else:
        log(f"  device row not found: {detail}")
        if not _confirm(
            f"Create device row '{device_id}' on {base_url} (admin REST, "
            "configuration " + repr(config_name) + ")?"
        ):
            log("aborted by owner — no server mutation")
            _result("OK", False, "ensure-device aborted by owner")
            return 0
        token = _jwt_login(base_url, admin_login, _read_admin_password())
        config_id, qr_key = _resolve_config(base_url, token, config_name)
        try:
            _create_device(base_url, token, device_id, config_id)
            created = True
        except RuntimeError as e:
            # The sync probe can be ahead of/behind the REST view; a
            # DEVICE_EXISTS refusal means the row is there — treat as
            # existing and continue (never overwrite; idempotent re-run).
            if "DEVICE_EXISTS" not in str(e):
                raise
            log_ok(f"server reports the number taken — row already exists ({e})")
        row_ok, detail = device_row_state(base_url, device_id)
        if row_ok:
            log_ok(f"device row confirmed via sync endpoint ({detail})")
        else:
            # PUT returned success; the sync view may need the agent's first
            # poll to settle — the enroll flow's poll gate is the real proof.
            log_warn(f"sync endpoint not confirming yet: {detail} — continuing")

    assert qr_key is not None
    init_path = _write_init_json(_build_init_json(base_url, qr_key, device_id))
    log_ok(f"init.json written: {init_path} (device {device_id})")

    # App URLs follow the canonical channel (D13 invariant): check-gated,
    # credential-free detection, mutation behind the same y/N + password
    # gate as the row create. A detection failure must not regress the
    # proven row+init.json guarantee — warn and continue instead.
    foreign: list[str] = []
    try:
        stale, foreign = _stale_app_urls(base_url, device_id)
    except RuntimeError as e:
        log_warn(f"app-URL staleness check skipped: {e}")
    for url in foreign:
        log_warn(f"app URL left as-is (foreign host, qa 1551): {url}")
    if stale:
        target = _retarget_url(stale[0]["url"], base_url)
        log_warn(
            f"app URLs stale ({len(stale)}) — server-issued URLs still point at "
            f"a dead channel; installs from them will hang/404"
        )
        if _confirm(
            f"Rewrite {len(stale)} application version URL(s) to the "
            f"canonical loopback URL (e.g. … → {target})?"
        ):
            if token is None:
                token = _jwt_login(base_url, admin_login, _read_admin_password())
            repointed = _repoint_app_urls(base_url, token, stale)
            still_stale, _ = _stale_app_urls(base_url, device_id)
            if still_stale:
                raise RuntimeError(
                    f"app URL re-point incomplete: {len(still_stale)} still stale"
                )
            log_ok(f"app URLs rewritten to the canonical channel ({repointed})")
        else:
            log(
                "aborted by owner — app URLs left stale (re-run --ensure-device "
                "with a password source to converge)"
            )
    elif not foreign:
        log_ok("app URLs: current")

    verb = "created" if created else "exists"
    app_state = "app URLs current" if not stale else f"{repointed} app URL(s) rewritten"
    _result(
        "OK",
        created or repointed > 0,
        f"device row {verb}; init.json written for {device_id}; {app_state}",
    )
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

    state, chain_msgs = channel_chain(apply=False)
    msgs.extend(chain_msgs)
    if state == "absent":
        msgs.append(
            "reverse channel not connected (phone unplugged) — forwards skipped"
        )
    elif state != "ok":
        ok = False

    # App URLs: read-only reporting — the convergence action lives in
    # --ensure-device; the trunk run never mutates the server.
    try:
        device_id, _ = load_device_vars()
        stale, _foreign = _stale_app_urls(agent_url(), device_id)
        msgs.append(
            "app URLs: current"
            if not stale
            else f"app URLs stale ({len(stale)}) — run --ensure-device"
        )
    except RuntimeError as e:
        if "error.notfound.device" in str(e):
            msgs.append("app URLs: device row not created yet — run --ensure-device")
        else:
            msgs.append(f"app URLs: check failed ({e})")
            ok = False

    unit_state, unit_msg = _user_unit_state(WATCHER_UNIT)
    msgs.append(unit_msg)
    if unit_state is False:
        ok = False

    return ok, msgs


# ── adb helpers ──────────────────────────────────────────────────────────────
def _adb(
    *args: str, serial: str | None = None, timeout: int = 60
) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return subprocess.run(
        [*cmd, *args], capture_output=True, text=True, check=False, timeout=timeout
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


# ── reverse-channel watcher (systemd user unit) ─────────────────────────────
WATCHER_UNIT = "mdm-channel.service"
WATCHER_SRC = REPO_ROOT / "etc" / "mdm" / "systemd" / "user" / WATCHER_UNIT
WATCHER_DEST = Path.home() / ".config" / "systemd" / "user" / WATCHER_UNIT


def _systemctl_user(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, check=False
    )


def _bus_unavailable(stderr: str) -> bool:
    return "Failed to connect" in stderr or "No medium found" in stderr


def _user_unit_state(unit: str) -> tuple[bool | None, str]:
    """(enabled_and_active, message). None = user bus unavailable (not checked)."""
    if _test_env("MDM_TEST_SKIP_WATCHER") == "1":
        return None, f"{unit}: watcher check skipped (MDM_TEST_SKIP_WATCHER)"
    p = _systemctl_user("is-enabled", unit)
    if _bus_unavailable(p.stderr or ""):
        return None, f"{unit}: user session unavailable (not checked)"
    if p.returncode != 0:
        return False, f"{unit}: not enabled — run ./install.py --only 67"
    a = _systemctl_user("is-active", unit)
    if a.returncode != 0:
        return False, f"{unit}: installed but inactive — run --ensure-channel"
    return True, f"{unit}: enabled and active"


def _ensure_watcher_running() -> tuple[bool, str]:
    """Start/enable the watcher if it is installed but down. Never raises."""
    if _test_env("MDM_TEST_SKIP_WATCHER") == "1":
        return False, "watcher check skipped (MDM_TEST_SKIP_WATCHER)"
    state, msg = _user_unit_state(WATCHER_UNIT)
    if state is None:
        return False, msg
    if state:
        return True, msg
    if not WATCHER_DEST.is_file():
        return False, f"{WATCHER_UNIT}: not installed — run ./install.py --only 67"
    p = _systemctl_user("enable", "--now", WATCHER_UNIT)
    if p.returncode != 0:
        return False, (
            f"{WATCHER_UNIT}: enable --now failed: {(p.stderr or p.stdout).strip()}"
        )
    return True, f"{WATCHER_UNIT}: enabled and started"


def ensure_linger() -> tuple[bool, str]:
    """The watcher is a user unit; linger makes it start at boot (verified on
    this host 2026-11-20). Self-linger is allowed by polkit — no sudo."""
    user = getpass.getuser()
    for attempt in ("check", "enable"):
        if attempt == "enable":
            subprocess.run(
                ["loginctl", "enable-linger", user],
                capture_output=True,
                text=True,
                check=False,
            )
        p = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
        if (p.stdout or "").strip() == "yes":
            return True, "linger enabled (watcher starts at boot)"
    return False, "linger not enabled — watcher starts at login only"


def install_channel_unit() -> bool:
    """Install/enable the watcher unit from the repo. Returns True on change."""
    if not WATCHER_SRC.is_file():
        raise RuntimeError(f"watcher unit source missing: {WATCHER_SRC}")
    content = WATCHER_SRC.read_text().replace("@REPO_ROOT@", str(REPO_ROOT))
    WATCHER_DEST.parent.mkdir(parents=True, exist_ok=True)
    changed = not WATCHER_DEST.is_file() or WATCHER_DEST.read_text() != content
    if changed:
        tmp = WATCHER_DEST.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.replace(WATCHER_DEST)
        p = _systemctl_user("daemon-reload")
        if p.returncode != 0:
            raise RuntimeError(
                "systemctl --user daemon-reload failed: "
                f"{(p.stderr or p.stdout).strip()}"
            )
    p = _systemctl_user("enable", "--now", WATCHER_UNIT)
    if p.returncode != 0:
        raise RuntimeError(
            f"systemctl --user enable --now {WATCHER_UNIT} failed: "
            f"{(p.stderr or p.stdout).strip()} — is this a logged-in user session?"
        )
    if changed:
        _systemctl_user("try-restart", WATCHER_UNIT)
    linger_ok, linger_msg = ensure_linger()
    if linger_ok:
        log_ok(linger_msg)
    else:
        log_warn(linger_msg)
    return changed


def run_ensure_channel() -> int:
    """Public repair entry point: apply forwards, verify both services, make
    sure the watcher is running. Idempotent."""
    log_step("ensure-channel: applying the canonical reverse channel (D13)")
    state, msgs = channel_chain(apply=True)
    for m in msgs:
        log(f"  {m}")
    if state != "ok":
        if state in ("absent", "unauthorized", "multiple", "partial"):
            _result("PARTIAL", False, f"reverse channel not ready: {state}")
            return 3
        _result("FAIL", False, f"reverse channel error: {state}")
        return 1
    watcher_ok, watcher_msg = _ensure_watcher_running()
    if watcher_ok:
        log_ok(watcher_msg)
    else:
        log_warn(watcher_msg)
    _result("OK", False, "reverse channel green (tcp:8080 + tcp:31000)")
    return 0


def run_watch_channel() -> int:
    """Internal service loop (ExecStart of the watcher unit). Not an operator
    command and deliberately absent from USAGE."""
    log_step("watch-channel: keeping the reverse forwards applied")
    last: str | None = None
    while True:
        try:
            state, _applied, msgs = ensure_forwards()
        except (OSError, subprocess.SubprocessError) as e:
            state, msgs = "error", [f"adb invocation failed: {e}"]
        line = f"{state}: {msgs[0]}" if msgs else state
        if line != last:
            log(f"  {line}")
            last = line
        time.sleep(WATCH_INTERVAL_S)


def _confirm(prompt: str) -> bool:
    if _test_env("MDM_TEST_YES") == "1":
        return True
    if not sys.stdin.isatty():
        raise _usage_error("confirmation requires an interactive terminal")
    print(f"{prompt} [y/N]: ", file=sys.stderr, end="", flush=True)
    line = sys.stdin.readline()
    if line == "":
        raise _usage_error("confirmation aborted: stdin closed")
    return line.strip().lower() in ("y", "yes")


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


# ── v2 policy sync (etc/mdm/policy/policy.json) ───────────────────────────────
def _load_policy() -> dict:
    """Delegate to the shared v2 policy validator."""
    return _shared_load_policy()


def _catalog(base_url: str, token: str) -> list[dict]:
    code, parsed = _http_json(
        f"{base_url}/rest/private/applications/search", token=token
    )
    if code != 200:
        raise RuntimeError(
            f"applications search failed — {_server_error(code, parsed)}"
        )
    apps = _envelope_data(parsed)
    if not isinstance(apps, list):
        raise RuntimeError(
            f"applications search: unexpected shape: {_shape_note(parsed)}"
        )
    return apps


def _config_apps(base_url: str, token: str, config_id: int) -> list[dict]:
    code, parsed = _http_json(
        f"{base_url}/rest/private/configurations/applications/{config_id}",
        token=token,
    )
    if code != 200:
        raise RuntimeError(
            f"config applications failed — {_server_error(code, parsed)}"
        )
    apps = _envelope_data(parsed)
    if not isinstance(apps, list):
        raise RuntimeError(
            f"config applications: unexpected shape: {_shape_note(parsed)}"
        )
    return apps


def _link_id(
    base_url: str, token: str, application_id: int, config_id: int
) -> int | None:
    code, parsed = _http_json(
        f"{base_url}/rest/private/applications/configurations/{application_id}",
        token=token,
    )
    if code != 200:
        raise RuntimeError(f"application links failed — {_server_error(code, parsed)}")
    links = _envelope_data(parsed)
    if not isinstance(links, list):
        raise RuntimeError(
            f"application links: unexpected shape: {_shape_note(parsed)}"
        )
    for item in links:
        if not isinstance(item, dict):
            continue
        cid = item.get("configurationId")
        if cid is not None and int(cid) == config_id:
            return int(item["id"])
    return None


def _plan_sync(policy: dict, catalog: list[dict], live_apps: list[dict]) -> dict:
    catalog_by_pkg = {a["pkg"]: a for a in catalog if a.get("pkg")}
    live = {a["pkg"]: a for a in live_apps if a.get("selected") and a.get("pkg")}
    create_apps: list[dict] = []
    link: list[dict] = []
    update: list[dict] = []
    unchanged: list[dict] = []
    for entry in policy["apps"]:
        pkg = entry["pkg"]
        want_action = int(entry.get("action", 1))
        want_icon = bool(entry.get("show_icon", False))
        if pkg not in catalog_by_pkg:
            create_apps.append(
                {
                    "pkg": pkg,
                    "name": entry.get("name", pkg),
                    "kind": entry.get("kind", "whitelist"),
                    "action": want_action,
                    "show_icon": want_icon,
                }
            )
        elif pkg not in live:
            link.append(
                {
                    "pkg": pkg,
                    "application_id": catalog_by_pkg[pkg]["id"],
                    "action": want_action,
                    "show_icon": want_icon,
                }
            )
        elif (
            int(live[pkg].get("action", 0)) != want_action
            or bool(live[pkg].get("showIcon")) != want_icon
        ):
            update.append(
                {
                    "pkg": pkg,
                    "application_id": live[pkg]["id"],
                    "action": want_action,
                    "show_icon": want_icon,
                }
            )
        else:
            unchanged.append({"pkg": pkg})
    return {
        "create_apps": create_apps,
        "link": link,
        "update": update,
        "unchanged": unchanged,
    }


def _create_application(base_url: str, token: str, entry: dict) -> int:
    body = {
        "name": entry["name"],
        "pkg": entry["pkg"],
        "type": "app",
        "version": "0",
        "versionCode": 0,
        "url": None,
        "system": entry.get("kind") == "keep_system",
    }

    def find_by_pkg() -> int | None:
        code, parsed = _http_json(
            f"{base_url}/rest/private/applications/search", token=token
        )
        if code != 200:
            raise RuntimeError(
                f"applications search failed — {_server_error(code, parsed)}"
            )
        apps = _envelope_data(parsed)
        if not isinstance(apps, list):
            raise RuntimeError(
                f"applications search: unexpected shape: {_shape_note(parsed)}"
            )
        for item in apps:
            if isinstance(item, dict) and item.get("pkg") == entry["pkg"]:
                return int(item["id"])
        return None

    try:
        code, parsed = _http_json(
            f"{base_url}/rest/private/applications/android",
            "PUT",
            body=body,
            token=token,
        )
    except Exception:
        found = find_by_pkg()
        if found is not None:
            return found
        raise
    app = _envelope_data(parsed)
    if isinstance(app, dict) and isinstance(app.get("id"), int):
        return app["id"]
    found = find_by_pkg()
    if found is not None:
        return found
    raise RuntimeError(f"application {entry['pkg']!r} not found after create")


def _apply_links(base_url: str, token: str, config_id: int, plan: dict) -> list[str]:
    messages: list[str] = []

    def post_link(entry: dict, link_id: int | None) -> None:
        pkg = entry["pkg"]
        action = int(entry["action"])
        show_icon = bool(entry["show_icon"])
        code, parsed = _http_json(
            f"{base_url}/rest/private/applications/configurations",
            "POST",
            body={
                "applicationId": entry["application_id"],
                "configurations": [
                    {
                        "applicationId": entry["application_id"],
                        "configurationId": config_id,
                        "action": action,
                        "showIcon": show_icon,
                        "id": link_id,
                        "notify": True,
                    }
                ],
            },
            token=token,
        )
        if not 200 <= code < 300:
            raise RuntimeError(f"link {pkg} failed — {_server_error(code, parsed)}")
        _envelope_data(parsed)  # HTTP 200 can still hide an envelope ERROR
        messages.append(f"linked {pkg} action={action} icon={show_icon}")

    for entry in plan["create_apps"]:
        app_id = _create_application(base_url, token, entry)
        link_entry = dict(entry)
        link_entry["application_id"] = app_id
        action = int(entry.get("action", 1))
        show_icon = bool(entry.get("show_icon", False))
        link_entry["action"] = action
        link_entry["show_icon"] = show_icon
        post_link(link_entry, None)

    for entry in plan["link"]:
        post_link(entry, None)

    for entry in plan["update"]:
        link_id = _link_id(base_url, token, entry["application_id"], config_id)
        if link_id is None:
            messages.append(f"cannot update {entry['pkg']}: link id not found")
            continue
        post_link(entry, link_id)

    return messages


def _apply_restrictions(
    base_url: str, token: str, config_id: int, restrictions: str
) -> list[str]:
    code, raw = _http_json(
        f"{base_url}/rest/private/configurations/{config_id}", token=token
    )
    if code != 200:
        raise RuntimeError(f"configuration fetch failed — {_server_error(code, raw)}")
    config = _envelope_data(raw)
    # PUT /rest/private/configurations is a full replace: only a real
    # configuration entity may be echoed back (its id must match the target).
    if not isinstance(config, dict) or config.get("id") != config_id:
        raise RuntimeError(f"configuration fetch failed — {_server_error(code, raw)}")
    apps = _config_apps(base_url, token, config_id)
    selected_apps = [a for a in apps if isinstance(a, dict) and a.get("selected")]
    config["applications"] = selected_apps
    # Preserve server-managed fields that getConfigurationById may omit; the
    # full-replace PUT must echo a complete entity (h-mdm requires it).
    # (applicationSettings/files already present in echoed entity; null is OK)
    already_correct = config.get("restrictions") == restrictions
    config["restrictions"] = restrictions
    if already_correct:
        return ["restrictions already correct"]
    code, parsed = _http_json(
        f"{base_url}/rest/private/configurations",
        "PUT",
        body=config,
        token=token,
    )
    if not 200 <= code < 300:
        raise RuntimeError(f"restrictions PUT failed — {_server_error(code, parsed)}")
    _envelope_data(parsed)  # HTTP 200 can still hide an envelope ERROR
    return [f"restrictions set: {restrictions}"]


def sync_policy(base_url: str, token: str, config_name: str) -> tuple[int, list[str]]:
    messages: list[str] = []
    try:
        policy = _load_policy()
        config_id, _key = _resolve_config(base_url, token, config_name)
        catalog = _catalog(base_url, token)
        live = _config_apps(base_url, token, config_id)
        plan = _plan_sync(policy, catalog, live)
        print(
            f"policy plan: create={len(plan['create_apps'])} "
            f"link={len(plan['link'])} update={len(plan['update'])} "
            f"unchanged={len(plan['unchanged'])}",
            file=sys.stderr,
        )
        messages.extend(_apply_links(base_url, token, config_id, plan))
        messages.extend(
            _apply_restrictions(base_url, token, config_id, policy["restrictions"])
        )
        _code, raw2 = _http_json(
            f"{base_url}/rest/private/configurations/{config_id}", token=token
        )
        config2 = _envelope_data(raw2)
        apps2 = _config_apps(base_url, token, config_id)
        selected = {a["pkg"]: a for a in apps2 if a.get("selected") and a.get("pkg")}
        for app in policy["apps"]:
            pkg = app["pkg"]
            live_app = selected.get(pkg)
            if live_app is None or int(live_app.get("action", 0)) != int(
                app.get("action", 1)
            ):
                messages.append(f"verify: {pkg} not linked correctly")
        if (
            not isinstance(config2, dict)
            or config2.get("restrictions") != policy["restrictions"]
        ):
            messages.append("verify: restrictions mismatch")
        if any(m.startswith("verify:") or m.startswith("cannot") for m in messages):
            return 1, messages
        return 0, messages
    except Exception as exc:
        return 1, messages + [f"policy sync failed: {exc}"]


# ── --enroll: guided ADB device-owner enrollment (C2, interactive) ───────────
def run_enroll(admin_login: str) -> int:
    # Preflight gates — each failure exits before any phone-side mutation.
    log_step("enroll preflight: laptop-side runtime facts")
    verify_prereqs()
    log_ok("adb present")
    stage_apk()

    units_ok, units_msg = units_active()
    if not units_ok:
        raise RuntimeError(f"preflight gate failed: {units_msg}")
    log_ok(units_msg)

    base_url = agent_url()
    log_step("enroll preflight: canonical reverse channel (D13)")
    channel_state, chain_msgs = channel_chain(apply=True)
    for m in chain_msgs:
        log_ok(m)
    if channel_state != "ok":
        raise RuntimeError(
            f"reverse channel not ready ({channel_state}): {'; '.join(chain_msgs)}"
        )

    # Facts stage — ALL server-side info in hand BEFORE anything touches
    # the phone (A7 end-to-end; read-only vs the server, credential-free).
    log_step("enroll facts: derive init.json from config.txt + server (A7)")
    init_path, device_id = acquire_init_payload()

    # Policy stage: apply etc/mdm/policy/policy.json to the server configuration
    # (y/N + admin password) before any device gate or phone-side action.
    policy = _load_policy()
    d10_violations = anti_coupling_violations(policy)
    if d10_violations:
        _result(
            "FAIL",
            False,
            f"policy data violates D10 anti-coupling: {d10_violations}",
        )
        return 1
    n_apps = len(policy["apps"])
    _, config_name = load_device_vars()
    base_url = agent_url()
    if not _confirm(
        f"Apply policy.json to configuration {config_name!r} ({n_apps} apps, restrictions)?"
    ):
        _result("OK", False, "policy sync skipped by owner")
    else:
        password = _read_admin_password()
        token = _jwt_login(base_url, admin_login, password)
        rc, messages = sync_policy(base_url, token, config_name)
        for m in messages:
            print(m, file=sys.stderr)
        if rc != 0:
            _result("FAIL", False, "policy sync failed")
            return 1

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
        log_ok(
            f"Android device owner already set: {existing_owner} — skipping "
            "install+grant; the server-side row is still verified by the poll gate"
        )
        log_warn(
            "this agent is already provisioned: init.json is read only on a "
            "clean agent start, so the push below may be ignored. To change "
            "the URL use the launcher key icon (GATE 18) or a factory reset "
            "(GATE 9); --enroll cannot re-point it in place."
        )
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

    # Stage 5-7: init.json push + agent start. The payload was derived and
    # written by the facts stage (BASE_URL = http://127.0.0.1:8080).
    _shell_out(_adb("push", str(init_path), INIT_JSON_ON_DEVICE), "adb push init.json")
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
    row_pre_existed, _detail = device_row_state(base_url, device_id)
    log_step(f"polling server for device row {device_id} (pipeline gate)")
    attempts = POLL_TIMEOUT_S // POLL_INTERVAL_S
    ok, detail = False, ""
    for n in range(1, attempts + 1):
        ok, detail = device_row_state(base_url, device_id)
        if ok:
            break
        log(f"  poll {n}/{attempts}: {detail}")
        time.sleep(POLL_INTERVAL_S)
    if not ok:
        log_error(
            f"device row NOT confirmed after {POLL_TIMEOUT_S}s (last: {detail}) "
            "— enrollment UNVERIFIED, ADB stays enabled. Check the agent on the "
            "phone (XOS battery manager may kill it), then re-run --enroll "
            "(idempotent) once it registers. Row pre-exists server-side, so "
            "an agent that never syncs is the suspect, not the row."
        )
        _result("PARTIAL", True, "enrollment unverified — ADB stays on (D13)")
        return 3
    log_ok(f"device row confirmed on server: {device_id}")
    if row_pre_existed:
        log(
            "  note: row existed before the agent started — a green poll is a "
            "pipeline check, NOT proof the phone synced. Proof: console "
            "last-sync turns fresh AND the phone shows the managed launcher."
        )

    hint = _stale_url_hint(base_url, device_id)
    if hint:
        log(f"  {hint}")
    _result("OK", True, f"enrolled; agent base URL {base_url}; ADB stays on (D13)")
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
USAGE = """Usage: python3 lib/67-mdm-provision.py [--verify|--enroll [--admin-login NAME]|--capture [--dry-run]|--ensure-device [--admin-login NAME]|--ensure-channel]
    (default run)   stage agent APK + install/enable the channel watcher +
                    convergence check (phone-free)
    --verify        convergence check (prereqs, APK pin, panel, MQTT, forwards,
                    app URLs, watcher); no sudo needed
    --enroll [--admin-login NAME]
                    guided ADB device-owner enrollment (interactive, C2);
                    preflight = reverse channel (both forwards applied →
                    Tomcat 200 → MQTT TCP connect); payload BASE_URL =
                    http://127.0.0.1:8080; asks y/N + the admin password to apply policy.json before the device handoff. On success,
                    prints the --ensure-device repair command when
                    server-issued app URLs are stale.
    --capture       §3.6 profile capture into ~/.local/state/mdm/profile-<ts>/
    --ensure-device guarantee server-side device row + init.json + app URLs
                    (server-issued URLs follow the canonical channel).
                    Credential-free when nothing needs fixing; mutations sit
                    behind y/N + the admin password (also used by --enroll's policy stage) via getpass prompt,
                    never config.txt
    --ensure-channel apply both adb reverse forwards, verify the panel + MQTT
                    broker, and start the watcher if inactive
    --admin-login NAME is accepted by both --enroll and --ensure-device
                    (default: admin).
"""


def main(argv: list[str]) -> int:
    args = argv[1:]
    if {"-h", "--help"} & set(args):
        print(USAGE)
        return 0

    try:
        _validate_args(args)
        admin_login = _parse_admin_login(args)
    except UsageError as exc:
        _result("FAIL", False, str(exc))
        return 1

    if not require_user("lib/67-mdm-provision.py", "python3 lib/67-mdm-provision.py"):
        return 1

    if is_vm():
        log_step("67-mdm-provision: running in VM, skipping")
        _result("SKIP", False, "in VM")
        return 2

    try:
        if "--watch-channel" in args:
            return run_watch_channel()

        if "--ensure-channel" in args:
            return run_ensure_channel()

        if "--enroll" in args:
            return run_enroll(admin_login)

        if "--capture" in args:
            return run_capture(dry_run="--dry-run" in args)

        if "--ensure-device" in args:
            return run_ensure_device(admin_login)

        if "--verify" in args:
            ok, msgs = converged()
            for m in msgs:
                log(f"  {m}")
            return 0 if ok else 3

        # Default trunk run: laptop-side only, idempotent, phone-free.
        verify_prereqs()
        changed = stage_apk()
        changed = install_channel_unit() or changed
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
