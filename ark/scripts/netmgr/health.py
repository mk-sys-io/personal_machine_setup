from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
from pathlib import Path

import opslog

from ._util import run

# Gomplate-templated constants
DNS_LISTEN_ADDR = "{{ .Env.DNSMASQ_LISTEN_ADDR }}"
DNS_LISTEN_PORT = int("{{ .Env.DNSMASQ_LISTEN_PORT }}")
DNS_HEALTH_TIMEOUT = int("{{ .Env.DNS_HEALTH_TIMEOUT }}")
NFT_DNS_PORTS = "{{ .Env.NFT_DNS_PORTS }}"
DNS_TEST_DOMAIN = "{{ .Env.DNS_TEST_DOMAIN }}"
RESOLV_CONF = "{{ .Env.RESOLV_CONF_PATH }}"
USER_UID = "{{ .Env.USER_UID }}"


def check_dnsmasq_alive() -> bool:
    try:
        sock = socket.create_connection(
            (DNS_LISTEN_ADDR, DNS_LISTEN_PORT),
            timeout=DNS_HEALTH_TIMEOUT,
        )
        sock.close()
        return True
    except (TimeoutError, OSError):
        return False


def check_nftables_rules(mode: str) -> bool:
    try:
        r = run(["nft", "list", "ruleset"], check=False)
        # Normalize the config value ("53,853" -> "53, 853") to match nft's
        # rendered set form; the needle is proto-agnostic so the udp+tcp rules
        # both count (restricted template emits two rules, unrestricted none).
        # Join with bare { } string literals; f-strings/format would emit
        # escaped braces that gomplate would treat as a template action.
        ports = ", ".join(p.strip() for p in NFT_DNS_PORTS.split(",") if p.strip())
        needle = " ".join(["skuid", USER_UID, "dport", "{", ports, "}", "drop"])
        dns_rules = len(re.findall(re.escape(needle), r.stdout))
        if mode == "unrestricted":
            return dns_rules == 0
        return dns_rules >= 2
    except (subprocess.SubprocessError, OSError):
        opslog.warn("could not query nftables ruleset")
        return False


def check_resolv_conf() -> bool:
    try:
        content = Path(RESOLV_CONF).read_text()
        return f"nameserver {DNS_LISTEN_ADDR}" in content
    except OSError:
        return False


def _resolve_as(user: str, host: str) -> bool:
    """Resolve `host` through system DNS as `user` (uses `su -` so we run
    under their nsswitch/netns context, mirroring verify.sh 135-170)."""
    try:
        argv = ["su", "-", user, "-c", f"getent hosts {shlex.quote(host)}"]
        r = run(argv, timeout=10, check=False)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def check_dns_leak(mode: str) -> bool:
    # Audit C4: run the probe as SUDO_USER — as root, the default netns
    # upstream would resolve any domain and locked mode would falsely pass.
    user = os.getenv("SUDO_USER") or os.getenv("USER") or "root"
    reachable = _resolve_as(user, DNS_TEST_DOMAIN)

    if mode == "locked":
        return not reachable
    return reachable


def check_root_dns() -> bool:
    try:
        run(["timeout", "5", "getent", "hosts", "github.com"], timeout=10)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def check_all(mode: str | None = None) -> dict[str, bool]:
    if mode is None:
        from mode import read
        mode = read()

    results = {
        "dnsmasq_alive": check_dnsmasq_alive(),
        "nftables_rules": check_nftables_rules(mode),
        "resolv_conf": check_resolv_conf(),
        "dns_leak": check_dns_leak(mode),
        "root_dns": check_root_dns(),
    }

    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        opslog.info("%s: %s", name, status)

    return results
