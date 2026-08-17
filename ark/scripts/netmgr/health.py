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


def nft_rule_count() -> int:
    """Count deployed DNS drop rules; -1 when the ruleset can't be queried.

    -1 is a fail-loud sentinel, never a count of zero — it keeps every
    verdict branch of check_nftables_rules False on a failed query.
    """
    try:
        r = run(["nft", "list", "ruleset"], check=False)
        # nft list renders the protocol token between the uid and dport
        # ("skuid 1000 udp dport { 53, 853 } drop") — the pattern must include
        # it or the count is always zero. (?:udp|tcp) matches both rules the
        # restricted template emits (two, unrestricted none).
        # Normalize the config value ("53,853" -> "53, 853") to match nft's
        # rendered set form. Join with bare escaped-brace string literals;
        # f-strings/format would emit escaped braces that gomplate would
        # treat as a template action.
        ports = ", ".join(p.strip() for p in NFT_DNS_PORTS.split(",") if p.strip())
        pattern = (
            "skuid\\s+" + re.escape(USER_UID)
            + "\\s+(?:udp|tcp)\\s+dport\\s*\\{"
            + "\\s*" + re.escape(ports) + "\\s*\\}\\s*drop"
        )
        return len(re.findall(pattern, r.stdout))
    except (subprocess.SubprocessError, OSError):
        opslog.warn("could not query nftables ruleset")
        return -1


def check_nftables_rules(mode: str) -> bool:
    matches = nft_rule_count()
    if mode == "unrestricted":
        return matches == 0
    return matches >= 2


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
        r = run(["timeout", "5", "getent", "hosts", DNS_TEST_DOMAIN],
                timeout=10, check=False)
        return r.returncode == 0
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
        if name == "dns_leak":
            opslog.info("%s: %s (%s)", name, status, DNS_TEST_DOMAIN)
        else:
            opslog.info("%s: %s", name, status)

    return results
