#!/usr/bin/env python3
"""MDM LAN substrate: dnsmasq LAN listener + dispatcher address claim.

Converges the LAN side of the C2 substrate (temporary spec
plans/active/android-lockdown/c2-lan-substrate-spec.md):

  1. Renders /etc/dnsmasq.d/mdm-lan.conf (bind-dynamic LAN listener on
     MDM_LAN_IP:53 answering MDM_DOMAIN with the laptop itself) — staged
     and merged-validated UNPRIVILEGED (`dnsmasq --test -7 <tmpdir>` over
     live drop-ins minus mdm-lan.conf plus candidate) BEFORE install; a
     final service-shape gate (`--test -7 $CONFIG_DIR`, stderr captured)
     gates the restart. Plain `dnsmasq --test` is drop-in-blind here
     (conf-dir commented out in /etc/dnsmasq.conf) and is never trusted.
  2. Renders the NetworkManager dispatcher hook
     /etc/NetworkManager/dispatcher.d/99-mdm-lan-address — the PRIMARY
     guarantee that the laptop holds MDM_LAN_IP on every hotspot profile
     (up/dhcp4-change/reapply -> RFC 5227 DAD probe -> ip addr add;
     down -> release only if self-added).
  3. Claims MDM_LAN_IP itself at run time when absent (same probe logic),
     then verifies the bind-dynamic auto-bind (listener appears WITHOUT a
     dnsmasq restart) and the LAN DNS answer.
  4. --heal: manual recovery — claim + verify on the declared subnet, or
     refuse + print the phone-side checklist on a foreign subnet (the
     phone's frozen static config cannot work there; hard limit).

Run standalone: python3 lib/68-mdm-lan.py [--heal|--verify]
Wired into install.py as 68-mdm-lan

The module never writes config.txt: missing/empty var = failure naming
the key; value changes are owner one-line edits + re-run of
./install.py --only 68 (real env overrides config.txt per its header).

Root work runs via `sudo -n` (the 64-mdm-tls pattern) — needs a cached
sudo ticket; the preflight fails fast with the `sudo -v` instruction
BEFORE any /etc rendering, so an expired ticket never half-installs.

Exit codes:
    0  converged (or foreign-subnet advisory handled)
    1  failure
    2  skip (in VM)
    3  partial (rendered but not converged / drift / claim blocked)
Stdlib only.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))  # for preconditions (ADR-011)

from preconditions import require_user  # noqa: E402


# ── log facades (63/64 pattern) ──────────────────────────────────────────────
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
MDM_LAN_CONF = Path("/etc/dnsmasq.d/mdm-lan.conf")
DISPATCHER_HOOK = Path("/etc/NetworkManager/dispatcher.d/99-mdm-lan-address")
DNMASQ_SERVICE = "dnsmasq"
DNSMASQ_BIN = "/usr/sbin/dnsmasq"  # /usr/sbin is not on the user PATH — absolute
DNSMASQ_DEFAULTS = Path("/etc/default/dnsmasq")
ARPING_PROBE_ARGS = ("-D", "-c", "3", "-w", "2")  # RFC 5227 DAD, ~2s max
REQUIRED_PKGS = ("iputils-arping",)


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


def _ensure_ticket() -> bool:
    """Fail fast BEFORE any /etc work when no cached sudo ticket (64 pattern)."""
    if _run_priv("true")[0] == 0:
        return True
    log_error(
        "no cached sudo ticket — run `sudo -v` (or ./install.py --only 68) "
        "in your terminal, then retry"
    )
    return False


def _iface_addrs(iface: str) -> list[dict]:
    """Global IPv4 addrs of iface as [{'ip': '1.2.3.4', 'prefixlen': 24}, ...]."""
    p = subprocess.run(
        ["ip", "-j", "-4", "addr", "show", "dev", iface],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        return []
    try:
        data = json.loads(p.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for entry in data:
        for info in entry.get("addr_info", []):
            if info.get("scope") == "global":
                out.append(
                    {"ip": info.get("local", ""), "prefixlen": info.get("prefixlen", 0)}
                )
    return out


def _iface_exists(iface: str) -> bool:
    return (Path("/sys/class/net") / iface).exists()


# ── config.txt (single source; never written) ────────────────────────────────
def _load_env() -> dict[str, str]:
    sys.path.insert(0, str(REPO_ROOT))
    from install import load_env  # noqa: E402

    return load_env()


def _required(env: dict[str, str], key: str) -> str:
    val = env.get(key, "").strip()
    if not val:
        raise RuntimeError(f"required var {key} is empty in config.txt")
    return val


def load_vars() -> dict:
    env = _load_env()
    ip_raw = _required(env, "MDM_LAN_IP")
    subnet_raw = _required(env, "MDM_LAN_SUBNET")
    iface = _required(env, "MDM_LAN_IFACE")
    domain = _required(env, "MDM_DOMAIN").strip().lower()
    if not re.match(r"^[a-z0-9.-]+$", domain):
        raise RuntimeError(f"MDM_DOMAIN looks invalid: {domain}")
    try:
        ip = ipaddress.ip_address(ip_raw)
    except ValueError as exc:
        raise RuntimeError(f"MDM_LAN_IP is not a valid IPv4 address: {ip_raw}") from exc
    if ip.version != 4:
        raise RuntimeError(f"MDM_LAN_IP must be IPv4, got {ip_raw}")
    try:
        subnet = ipaddress.ip_network(subnet_raw, strict=False)
    except ValueError as exc:
        raise RuntimeError(
            f"MDM_LAN_SUBNET is not a valid network: {subnet_raw}"
        ) from exc
    if subnet.version != 4:
        raise RuntimeError(f"MDM_LAN_SUBNET must be IPv4, got {subnet_raw}")
    if ip not in subnet:
        raise RuntimeError(
            f"MDM_LAN_IP {ip_raw} is not inside MDM_LAN_SUBNET {subnet_raw} — "
            "fix config.txt"
        )
    dns_primary = env.get("DNS_PRIMARY", "").strip() or "1.1.1.1"
    if not _iface_exists(iface):
        raise RuntimeError(
            f"MDM_LAN_IFACE {iface} does not exist (/sys/class/net) — "
            "fix config.txt or override the env var for this run"
        )
    return {
        "ip": str(ip),
        "subnet": subnet,
        "iface": iface,
        "domain": domain,
        "dns_primary": dns_primary,
    }


# ── render targets ───────────────────────────────────────────────────────────
def mdm_lan_conf_content(v: dict) -> str:
    return (
        "# managed by lib/68-mdm-lan.py — values from config.txt (## MDM LAN)\n"
        "# Do not edit: re-rendered by ./install.py --only 68 (drift is reverted).\n"
        "# LAN override: answer the MDM domain with the laptop itself; forward\n"
        "# everything else upstream. bind-dynamic tolerates the address being\n"
        "# absent at startup and auto-binds it when the dispatcher claims it.\n"
        f"bind-dynamic\n"
        f"listen-address={v['ip']}\n"
        # NO port=53: allowlist.conf owns port 53 (the default is 53 anyway)
        # and dnsmasq forbids repeated single-occurrence keywords even with
        # an identical value ("illegal repeated keyword" — 2026-11-14 outage).
        f"address=/{v['domain']}/{v['ip']}\n"
        f"server={v['dns_primary']}\n"
        f"no-dhcp-interface={v['iface']}\n"
        "no-hosts\n"
        "domain-needed\n"
        "bogus-priv\n"
    )


def dispatcher_content(v: dict) -> str:
    return f"""#!/bin/bash
# /etc/NetworkManager/dispatcher.d/99-mdm-lan-address
# Managed by lib/68-mdm-lan.py (rendered from config.txt ## MDM LAN).
# Do not edit — re-render via ./install.py --only 68.
# PRIMARY guarantee for the MDM LAN address: claims the address on every
# hotspot profile (up/dhcp4-change/reapply), RFC 5227 DAD-probed, and
# releases it on down only if this hook added it. Runs as root (no sudo).
set -u
MDM_LAN_IP="{v["ip"]}"
MDM_LAN_PREFIX="{v["subnet"].prefixlen}"
LAN_NET_PREFIX="{str(v["subnet"].network_address).rsplit(".", 1)[0]}."
MDM_LAN_IFACE="{v["iface"]}"   # fallback; dispatcher DEVICE_IFACE env wins
MARKER="/run/mdm-lan-claimed"

iface="${{DEVICE_IFACE:-$MDM_LAN_IFACE}}"
action="${{NM_DISPATCHER_ACTION:-}}"
log() {{ logger -t mdm-lan "$*"; }}

[ "$iface" = "$MDM_LAN_IFACE" ] || exit 0

addr_present() {{
    ip -4 -o addr show dev "$iface" 2>/dev/null \\
        | awk '{{print $4}}' | grep -qxF "${{MDM_LAN_IP}}/${{MDM_LAN_PREFIX}}"
}}

case "$action" in
up|dhcp4-change|reapply)
    addr_present && exit 0
    # Subnet guard: claim only when the interface's global IPv4 matches the
    # declared LAN (dotted prefix + prefix length). On a foreign subnet the
    # claim is refused — lib/68-mdm-lan.py --heal advises phone-side.
    net_ip="$(ip -4 -o addr show dev "$iface" scope global 2>/dev/null | head -n1 | awk '{{print $4}}')"
    [ -n "$net_ip" ] || exit 0
    case "${{net_ip%%/*}}" in
        "${{LAN_NET_PREFIX}}"*) ;;
        *) exit 0 ;;
    esac
    case "${{net_ip#*/}}" in
        "$MDM_LAN_PREFIX") ;;
        *) exit 0 ;;
    esac
    # RFC 5227 DAD probe: abort the claim if another host answers
    # (fail-closed — an ambiguous DNS target is worse than no listener).
    if ! arping -D -c 3 -w 2 -I "$iface" "$MDM_LAN_IP" >/dev/null 2>&1; then
        log "conflict: another host answers $MDM_LAN_IP on $iface — claim aborted (diagnose with lib/68-mdm-lan.py --heal)"
        exit 0
    fi
    if ip addr add "$MDM_LAN_IP/$MDM_LAN_PREFIX" dev "$iface" 2>/dev/null; then
        touch "$MARKER"
        log "claimed $MDM_LAN_IP/$MDM_LAN_PREFIX on $iface ($action)"
    else
        log "ERROR: ip addr add $MDM_LAN_IP/$MDM_LAN_PREFIX dev $iface failed"
    fi
    ;;
down)
    # Release only what this hook added; NM-owned addresses survive.
    if [ -f "$MARKER" ]; then
        if addr_present; then
            ip addr del "$MDM_LAN_IP/$MDM_LAN_PREFIX" dev "$iface" 2>/dev/null \\
                && log "released $MDM_LAN_IP on $iface (self-added)"
        fi
        rm -f "$MARKER"
    fi
    ;;
esac
exit 0
"""


def _install_file(path: Path, content: str, mode: str) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(content)
        tmp = Path(tf.name)
    try:
        rc, out = _run_priv(
            "install", "-D", "-m", mode, "-o", "root", "-g", "root", str(tmp), str(path)
        )
        if rc != 0:
            raise RuntimeError(f"writing {path} failed: {out.strip()}")
    finally:
        tmp.unlink(missing_ok=True)


def file_matches(path: Path, content: str, mode: str) -> bool:
    """True when path already has exactly this content and mode (no drift)."""
    rc, out = _run_priv("cat", str(path))
    if rc != 0 or out != content:
        return False
    rc_s, out_s = _run_priv("stat", "-c", "%a", str(path))
    return rc_s == 0 and out_s.strip() in (mode, mode.lstrip("0"))


# ── dnsmasq validation (Layer 1, spec §3.3) ──────────────────────────────────
def config_dir() -> str:
    """CONFIG_DIR from /etc/default/dnsmasq — what the unit passes as -7.

    Falls back to /etc/dnsmasq.d when the file or the var is absent.
    """
    try:
        text = DNSMASQ_DEFAULTS.read_text(encoding="utf-8")
    except OSError:
        return "/etc/dnsmasq.d"
    m = re.search(r"^CONFIG_DIR=(.*)$", text, re.MULTILINE)
    if m:
        val = m.group(1).strip().strip('"').strip("'").strip()
        if val:
            return val
    return "/etc/dnsmasq.d"


def dnsmasq_merged_test(v: dict) -> tuple[bool, str]:
    """Validate the candidate conf against the live drop-in set, UNPRIVILEGED.

    Stages live drop-ins (minus mdm-lan.conf) + the candidate into a temp
    dir and runs `dnsmasq --test -7 <tmpdir>`. Plain `dnsmasq --test` is
    drop-in-blind on this packaging (conf-dir commented out in
    /etc/dnsmasq.conf; drop-ins load only via the unit's -7) and
    false-passed the port=53 repeat — this is the fix. Only the first
    (absolute) CONFIG_DIR entry is staged; the relative .dpkg-* entries
    carry dpkg leftovers only and are covered by the service-shape gate.
    """
    src_dir = Path(config_dir().split(",")[0])
    with tempfile.TemporaryDirectory(prefix="mdm-lan-test.") as td:
        tmp = Path(td)
        try:
            for f in sorted(src_dir.iterdir()):
                if f.is_file() and f.name != MDM_LAN_CONF.name:
                    (tmp / f.name).write_text(
                        f.read_text(encoding="utf-8"), encoding="utf-8"
                    )
        except (OSError, UnicodeDecodeError) as exc:
            return False, f"staging {src_dir} failed: {exc}"
        (tmp / MDM_LAN_CONF.name).write_text(mdm_lan_conf_content(v), encoding="utf-8")
        p = subprocess.run(
            [DNSMASQ_BIN, "--test", "-7", str(tmp)],
            capture_output=True,
            text=True,
            check=False,
        )
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode == 0:
        return True, out or "merged staging validation clean"
    return False, out or f"{DNSMASQ_BIN} --test -7 <staged> failed rc={p.returncode}"


def dnsmasq_service_test() -> tuple[bool, str]:
    """Final gate in the EXACT service shape: --test -7 $CONFIG_DIR.

    The unit's own checkconfig discards stderr
    (/usr/share/dnsmasq/init-system-common) — capture and print it here.
    """
    conf_dir = config_dir()
    p = subprocess.run(
        [DNSMASQ_BIN, "--test", "-7", conf_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode == 0:
        return True, out or f"--test -7 {conf_dir} clean"
    return False, out or f"--test -7 {conf_dir} failed rc={p.returncode}"


# ── claim (shared by default run and --heal; dispatcher does the same) ───────
def claim_address(v: dict) -> tuple[bool, str]:
    """Ensure MDM_LAN_IP is on the iface. Returns (ok, message)."""
    iface, ip, subnet = v["iface"], v["ip"], v["subnet"]
    addrs = _iface_addrs(iface)
    want = f"{ip}/{subnet.prefixlen}"
    if any(f"{a['ip']}/{a['prefixlen']}" == want for a in addrs):
        return True, f"{ip} already on {iface}"
    if not any(ipaddress.ip_address(a["ip"]) in subnet for a in addrs):
        return False, (
            f"{iface} is not inside {subnet} (foreign subnet) — claim refused; "
            "phone-side checklist applies (--heal prints it)"
        )
    rc, out = _run_priv("arping", *ARPING_PROBE_ARGS, "-I", iface, ip)
    if rc != 0:
        return False, (
            f"conflict: another host answers {ip} on {iface} (arping rc={rc}) — "
            "claim aborted (fail-closed)"
        )
    rc, out = _run_priv("ip", "addr", "add", want, "dev", iface)
    if rc != 0:
        return False, f"ip addr add {want} dev {iface} failed: {out.strip()}"
    log_ok(f"claimed {want} on {iface} (DAD-probed)")
    return True, f"claimed {want} on {iface}"


def wait_listener(v: dict, timeout: float = 10.0) -> bool:
    """bind-dynamic auto-bind: the listener must appear WITHOUT a restart."""
    target = f"{v['ip']}:53"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc, out = _run_priv("ss", "-lun")
        if rc == 0 and any(target in line for line in out.splitlines()):
            return True
        time.sleep(0.5)
    return False


def dig_answer(v: dict) -> tuple[bool, str]:
    rc, out = _run_priv(
        "dig", f"@{v['ip']}", v["domain"], "+short", "+time=3", "+tries=1"
    )
    if rc != 0:
        return False, f"dig @{v['ip']} failed: {out.strip() or f'rc={rc}'}"
    answers = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if answers and answers[0] == v["ip"]:
        return True, f"dig @{v['ip']} {v['domain']} -> {v['ip']}"
    return False, (
        f"dig @{v['ip']} returned {answers or 'nothing'} (expected {v['ip']})"
    )


def nm_profile_note(v: dict) -> None:
    """Verify-only: report the optional NM profile address (never applied here)."""
    try:
        p = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"],
            capture_output=True,
            text=True,
            check=False,
        )
        name = ""
        for line in (p.stdout or "").splitlines():
            f = line.split(":")
            if len(f) >= 2 and f[1] == v["iface"]:
                name = f[0]
                break
        if not name:
            return
        p2 = subprocess.run(
            ["nmcli", "-t", "-f", "ipv4.addresses", "con", "show", name],
            capture_output=True,
            text=True,
            check=False,
        )
        if v["ip"] in (p2.stdout or ""):
            log_ok(f"NM profile '{name}' carries {v['ip']} (belt-and-braces present)")
        else:
            log(
                f"  note: NM profile '{name}' has no {v['ip']} address "
                "(optional belt-and-braces; owner one-liner per spec §3.2)"
            )
    except OSError:
        log_warn("nmcli unavailable — skipping NM profile note")


# ── verify-only chain (no mutation) ──────────────────────────────────────────
def verify_chain(v: dict) -> tuple[bool, list[str], bool]:
    """(ok, messages, address_present). Checks files, unit, listener, dig."""
    msgs: list[str] = []
    ok = True

    for path, content, what in (
        (MDM_LAN_CONF, mdm_lan_conf_content(v), "mdm-lan.conf"),
        (DISPATCHER_HOOK, dispatcher_content(v), "dispatcher hook"),
    ):
        rc, out = _run_priv("cat", str(path))
        if rc != 0:
            msgs.append(f"{what} missing ({path})")
            ok = False
        elif out != content:
            msgs.append(f"{what} drifted from render (re-run ./install.py --only 68)")
            ok = False
        else:
            msgs.append(f"{what} matches render")

    rc, out = _run_priv("systemctl", "is-active", DNMASQ_SERVICE)
    if out.strip() == "active":
        msgs.append(f"{DNMASQ_SERVICE} unit active")
    else:
        msgs.append(f"{DNMASQ_SERVICE} unit not active ({out.strip() or rc})")
        ok = False

    # Plain `dnsmasq --test` is drop-in-blind (§2) — service-shape only.
    svc_ok, svc_msg = dnsmasq_service_test()
    msgs.append(f"service-shape --test -7 $CONFIG_DIR: {svc_msg}")
    ok = ok and svc_ok

    addrs = _iface_addrs(v["iface"])
    present = any(
        f"{a['ip']}/{a['prefixlen']}" == f"{v['ip']}/{v['subnet'].prefixlen}"
        for a in addrs
    )
    if present:
        msgs.append(f"{v['ip']}/{v['subnet'].prefixlen} present on {v['iface']}")
        rc, out = _run_priv("ss", "-lun")
        if any(f"{v['ip']}:53" in line for line in out.splitlines()):
            msgs.append(f"listener {v['ip']}:53 up")
            dig_ok, dig_msg = dig_answer(v)
            msgs.append(dig_msg)
            ok = ok and dig_ok
        else:
            msgs.append(f"listener {v['ip']}:53 absent — dnsmasq restart needed?")
            ok = False
    else:
        msgs.append(
            f"{v['ip']} absent from {v['iface']} (dispatcher claims on next "
            "up/dhcp4-change; or run --heal)"
        )
        ok = False
    return ok, msgs, present


# ── --heal ───────────────────────────────────────────────────────────────────
def print_phone_checklist(v: dict, detected_subnet: str | None) -> None:
    ns = detected_subnet or "<current-subnet>"
    print("\n" + "=" * 78)
    print(f"FOREIGN SUBNET: {v['iface']} is on {ns}, declared LAN is {v['subnet']}")
    print("The phone's frozen static config cannot work here (hard limit).")
    print("\nIf THIS network is now primary, do (order matters):")
    print(f"  1. Phone Wi-Fi static config: IP <{ns.rsplit('.', 1)[0]}>.150/24,")
    print(f"     gateway <{ns.rsplit('.', 1)[0]}>.1, DNS <{ns.rsplit('.', 1)[0]}>.139")
    print("     (same last octets as at home; the DNS pin must change too).")
    print(
        "  2. config.txt: MDM_LAN_SUBNET=" + ns + " and MDM_LAN_IP="
        f"{ns.rsplit('.', 1)[0]}.139"
        "  (keep phone DNS in step 1 equal to it)"
    )
    print("  3. ./install.py --only 68   (re-renders conf + dispatcher hook)")
    print("  4. Toggle phone airplane mode, then re-check the agent sync.")
    print("When back on the home Wi-Fi: revert config.txt and re-run --only 68.")
    print("Nothing was claimed or mutated on this network (advisory only).")
    print("=" * 78 + "\n")


def run_heal(v: dict) -> int:
    addrs = _iface_addrs(v["iface"])
    nets = {
        str(ipaddress.ip_network(f"{a['ip']}/{a['prefixlen']}", strict=False))
        for a in addrs
    }
    declared = str(v["subnet"])
    if declared not in nets:
        print_phone_checklist(v, sorted(nets)[0] if nets else None)
        _result("OK", False, "foreign subnet — advisory printed, nothing claimed")
        return 0
    log_step(f"--heal: on declared subnet {declared}")
    ok, msg = claim_address(v)
    log(f"  {msg}")
    if not ok:
        log_warn("claim blocked — see message above")
        _result("PARTIAL", False, msg)
        return 3
    if not wait_listener(v):
        log_error(
            f"listener {v['ip']}:53 did not appear without a restart "
            "(bind-dynamic auto-bind failed?)"
        )
        _result("PARTIAL", True, "claimed but listener absent")
        return 3
    log_ok(f"listener {v['ip']}:53 up (auto-bind, no restart)")
    dig_ok, dig_msg = dig_answer(v)
    log(f"  {dig_msg}")
    _result("OK", True, "heal converged" if dig_ok else "heal claimed; dig failed")
    return 0 if dig_ok else 3


# ── default run ──────────────────────────────────────────────────────────────
def run_converge(v: dict) -> int:
    if not _ensure_ticket():
        _result("FAIL", False, "sudo unavailable")
        return 1

    missing = []
    for pkg in REQUIRED_PKGS:
        rc, out = _run_priv("dpkg-query", "-W", "-f=${Status}", pkg)
        if rc != 0 or "install ok installed" not in out:
            missing.append(pkg)
    if missing:
        raise RuntimeError(
            f"missing packages: {', '.join(missing)} — "
            f"run ./install.py --only 20 or add them to packages/apt.txt"
        )

    conf_content = mdm_lan_conf_content(v)
    hook_content = dispatcher_content(v)
    conf_ok = file_matches(MDM_LAN_CONF, conf_content, "0644")
    hook_ok = file_matches(DISPATCHER_HOOK, hook_content, "0700")
    changed = not (conf_ok and hook_ok)

    if not hook_ok:
        log_step(f"rendering NM dispatcher hook -> {DISPATCHER_HOOK}")
        _install_file(DISPATCHER_HOOK, hook_content, "0700")
        log_ok("dispatcher hook rendered")

    if not conf_ok:
        # Layer 1: validate the candidate against the live drop-in set
        # BEFORE touching /etc — plain `dnsmasq --test` never reads
        # drop-ins on this packaging and false-passed the port=53 repeat.
        stage_ok, stage_msg = dnsmasq_merged_test(v)
        log(f"  merged staging: {stage_msg}")
        if not stage_ok:
            raise RuntimeError(
                "merged staging validation failed — mdm-lan.conf NOT "
                "installed, dnsmasq untouched (fix config.txt and re-run)"
            )
        log_ok("merged staging validation clean (--test -7 <staged drop-ins>)")
        log_step(f"rendering dnsmasq mdm-lan.conf -> {MDM_LAN_CONF}")
        _install_file(MDM_LAN_CONF, conf_content, "0644")
        log_ok("mdm-lan.conf rendered")

        # Final gate in the EXACT service shape (the unit runs
        # --test -7 $CONFIG_DIR; its checkconfig discards stderr —
        # capture and print it here). Restart only on green.
        svc_ok, svc_msg = dnsmasq_service_test()
        log(f"  service-shape gate: {svc_msg}")
        if not svc_ok:
            raise RuntimeError(
                f"service-shape {DNSMASQ_BIN} --test -7 $CONFIG_DIR failed "
                "(stderr above) — restart ABORTED, running dnsmasq untouched"
            )
        log_ok("service-shape gate clean — safe to restart")
        rc, out = _run_priv("systemctl", "restart", DNMASQ_SERVICE)
        if rc != 0:
            raise RuntimeError(
                f"systemctl restart {DNMASQ_SERVICE} failed: {out.strip()}"
            )
        log_ok(f"{DNMASQ_SERVICE} restarted (conf content changed)")

    ok, msg = claim_address(v)
    log(f"  {msg}")

    if not wait_listener(v):
        log_error(
            f"listener {v['ip']}:53 absent without a restart "
            "(auto-bind failed or claim blocked)"
        )
        _result("PARTIAL", changed, "listener absent — see messages")
        return 3
    log_ok(f"listener {v['ip']}:53 up (bind-dynamic auto-bind, no restart)")

    dig_ok, dig_msg = dig_answer(v)
    log(f"  {dig_msg}")
    nm_profile_note(v)

    if ok and dig_ok:
        _result(
            "OK", changed, "mdm-lan converged (conf + dispatcher + listener + LAN DNS)"
        )
        return 0
    _result("PARTIAL", changed, "not fully converged — see messages")
    return 3


def run_verify(v: dict) -> int:
    if not _ensure_ticket():
        _result("FAIL", False, "sudo unavailable for --verify")
        return 1
    ok, msgs, present = verify_chain(v)
    for m in msgs:
        log(f"  {m}")
    nm_profile_note(v)
    if ok:
        _result("OK", False, "mdm-lan verify: converged")
        return 0
    hint = "address absent (pre-claim state)" if not present else "drift/listener/dig"
    _result("PARTIAL", False, f"verify: not converged ({hint})")
    return 3


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    if {"-h", "--help"} & set(argv[1:]):
        print("Usage: python3 lib/68-mdm-lan.py [--heal|--verify]")
        return 0

    if not require_user("lib/68-mdm-lan.py", "python3 lib/68-mdm-lan.py"):
        return 1

    if is_vm():
        log_step("68-mdm-lan: running in VM, skipping")
        _result("SKIP", False, "in VM")
        return 2

    try:
        v = load_vars()

        if "--heal" in argv[1:]:
            return run_heal(v)
        if "--verify" in argv[1:]:
            return run_verify(v)
        return run_converge(v)

    except Exception as exc:
        log_error(f"68-mdm-lan: {exc}")
        _result("FAIL", False, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
