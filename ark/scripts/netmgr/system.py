"""System-level DNS configuration — NetworkManager, resolv.conf, podman.

Absorbs the DNS setup from lib/40-system_config.sh (lines 19-48).
These are install-time operations that configure the host to use
local dnsmasq and containers to bypass it.
"""
from __future__ import annotations

from pathlib import Path

import opslog

from ._util import run

# Gomplate-templated constants
DNS_UPSTREAM_V4 = "{{ .Env.DNS_PRIMARY }}"
DNS_LISTEN_ADDR = "{{ .Env.DNSMASQ_LISTEN_ADDR }}"
RESOLV_CONF = "{{ .Env.RESOLV_CONF_PATH }}"
NM_DNS_CONF = "{{ .Env.NETWORK_MANAGER_DNS_CONF }}"
PODMAN_DNS_CONF = "{{ .Env.PODMAN_DNS_CONF }}"


def setup_dns() -> None:
    """Configure NetworkManager dns=none + resolv.conf -> 127.0.0.1 + chattr +i."""
    _configure_network_manager()
    _write_resolv_conf()
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


def _configure_network_manager() -> None:
    nm_dir = Path(NM_DNS_CONF).parent
    nm_dir.mkdir(parents=True, exist_ok=True)
    Path(NM_DNS_CONF).write_text("[main]\ndns=none\n")
    run(["chown", "root:root", NM_DNS_CONF])
    run(["chmod", "644", NM_DNS_CONF])


def _write_resolv_conf() -> None:
    run(["chattr", "-i", RESOLV_CONF], check=False)
    Path(RESOLV_CONF).write_text(f"nameserver {DNS_LISTEN_ADDR}\n")


def _make_immutable() -> None:
    run(["chattr", "+i", RESOLV_CONF])
