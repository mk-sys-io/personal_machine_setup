"""System-level DNS configuration — NetworkManager, resolv.conf, podman.

Absorbs the DNS setup from lib/40-system_config.sh (lines 19-48).
These are install-time operations that configure the host to use
local dnsmasq and containers to bypass it.
"""
from __future__ import annotations

import socket
import struct
from pathlib import Path

import immutable_lib
import opslog

from ._util import run

# Gomplate-templated constants
DNS_UPSTREAM_V4 = "{{ .Env.DNS_PRIMARY }}"
DNS_LISTEN_ADDR = "{{ .Env.DNSMASQ_LISTEN_ADDR }}"
DNS_LISTEN_PORT = int("{{ .Env.DNSMASQ_LISTEN_PORT }}")
RESOLV_CONF = "{{ .Env.RESOLV_CONF_PATH }}"
NM_DNS_CONF = "{{ .Env.NETWORK_MANAGER_DNS_CONF }}"
PODMAN_DNS_CONF = "{{ .Env.PODMAN_DNS_CONF }}"
PROBE_HOST = "{{ .Env.DNS_TEST_DOMAIN }}"


def setup_dns() -> None:
    """Configure NetworkManager dns=none + resolv.conf -> 127.0.0.1 + chattr +i."""
    _ensure_dnsmasq_upstream()
    _configure_network_manager()
    previous = _read_resolv_conf()
    _write_resolv_conf()
    if not _probe_loopback():
        _restore_resolv_conf(previous)
        raise RuntimeError(
            f"loopback DNS probe failed ({DNS_LISTEN_ADDR}:{DNS_LISTEN_PORT} for "
            f"{PROBE_HOST}) — resolv.conf restored; dnsmasq cannot forward "
            "(check /etc/dnsmasq.d/allowlist.conf)"
        )
    _make_immutable()
    opslog.info("system DNS configured")


def setup_podman_dns() -> None:
    """Set podman container DNS servers (containers bypass host dnsmasq)."""
    conf_dir = Path(PODMAN_DNS_CONF).parent
    conf_dir.mkdir(parents=True, exist_ok=True)
    content = f'[containers]\ndns_servers = ["{DNS_UPSTREAM_V4}"]\n'
    Path(PODMAN_DNS_CONF).write_text(content)
    run(["chown", "root:root", PODMAN_DNS_CONF])
    run(["chmod", "644", PODMAN_DNS_CONF])
    opslog.info("podman DNS set to %s", DNS_UPSTREAM_V4)


def _ensure_dnsmasq_upstream() -> None:
    """dnsmasq must be able to forward BEFORE resolv.conf points at it.

    2026-10-19 regression: setup_dns used to chattr-resolv.conf at
    127.0.0.1 while dnsmasq had no server=/no-resolv of its own
    (allowlist.conf only ever written by ark enable/disable) — all
    resolution died and +i blocked dhcpcd self-healing. Now: if
    allowlist.conf is missing, write the unrestricted upstream config
    (dns.configure restarts + verifies dnsmasq) first. An existing file
    is left alone so re-runs never clobber a focused/locked policy.
    """
    from . import dns  # function-level: avoids import cycle at module load

    if Path(dns.DNSMASQ_CONF).is_file():
        return
    opslog.info("%s missing — writing unrestricted upstream config first", dns.DNSMASQ_CONF)
    dns.configure("unrestricted")


def _configure_network_manager() -> None:
    nm_dir = Path(NM_DNS_CONF).parent
    nm_dir.mkdir(parents=True, exist_ok=True)
    Path(NM_DNS_CONF).write_text("[main]\ndns=none\n")
    run(["chown", "root:root", NM_DNS_CONF])
    run(["chmod", "644", NM_DNS_CONF])


def _read_resolv_conf() -> str | None:
    p = Path(RESOLV_CONF)
    try:
        return p.read_text() if p.exists() else None
    except OSError:
        return None


def _write_resolv_conf() -> None:
    immutable_lib.clear_immutable(RESOLV_CONF, strict=False)
    Path(RESOLV_CONF).write_text(f"nameserver {DNS_LISTEN_ADDR}\n")


def _restore_resolv_conf(previous: str | None) -> None:
    if previous is None:
        return
    immutable_lib.clear_immutable(RESOLV_CONF, strict=False)
    Path(RESOLV_CONF).write_text(previous)


def _probe_loopback(timeout: float = 5.0) -> bool:
    """Single A-record query straight at the dnsmasq listen addr (stdlib only).

    dig/nslookup/dnsutils are not guaranteed installed; a 12-line UDP query
    is. Queries the server directly (not via resolv.conf) so the probe
    validates dnsmasq forwarding regardless of who owns resolv.conf.
    """
    txid = 0x5A17
    header = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        bytes([len(label)]) + label.encode("ascii") for label in PROBE_HOST.split(".")
    ) + b"\x00"
    question = qname + struct.pack(">HH", 1, 1)  # type A, class IN
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(header + question, (DNS_LISTEN_ADDR, DNS_LISTEN_PORT))
        data, _addr = sock.recvfrom(512)
    except OSError as exc:
        opslog.error("loopback DNS probe: %s", exc)
        return False
    finally:
        sock.close()
    if len(data) < 12:
        opslog.error("loopback DNS probe: short reply (%d bytes)", len(data))
        return False
    rtxid, flags, _qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rtxid != txid or not (flags & 0x8000) or an < 1:  # QR set + nonempty answer
        opslog.error(
            "loopback DNS probe: bad reply (txid=%#x flags=%#x answers=%d)",
            rtxid,
            flags,
            an,
        )
        return False
    return True


def _make_immutable() -> None:
    immutable_lib.set_immutable(RESOLV_CONF)
