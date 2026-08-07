from __future__ import annotations

import time
from pathlib import Path

import opslog

from ._util import run

# Gomplate-templated constants
ARK_DATA = "{{ .Env.ARK_DATA_PATH }}"
NFTABLES_CONF = "{{ .Env.NFTABLES_CONF_PATH }}"
NFTABLES_SERVICE = "{{ .Env.NFTABLES_SERVICE }}"
DNSMASQ_SERVICE = "{{ .Env.DNSMASQ_SERVICE }}"
DNS_HEALTH_DELAY = int("{{ .Env.DNS_HEALTH_DELAY }}")


def apply(mode: str) -> None:
    """Deploy nftables template for mode, restart, health-check dnsmasq."""
    _deploy_template(mode)
    _restart()
    _check_dns_health()
    opslog.info("nftables applied for %s mode", mode)


def _deploy_template(mode: str) -> None:
    if mode == "unrestricted":
        src = Path(ARK_DATA) / "nftables.conf.base"
    else:
        src = Path(ARK_DATA) / "nftables.conf.restricted"
    dest = Path(NFTABLES_CONF)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(src.read_text())
    tmp.rename(dest)
    dest.chmod(0o644)
    opslog.debug("deployed %s -> %s", src.name, dest)


def _restart() -> None:
    run(["systemctl", "restart", NFTABLES_SERVICE])


def _check_dns_health() -> None:
    """Post-deploy: verify dnsmasq is reachable on 127.0.0.1:53."""
    # Function-level import to avoid circular dependency: dns → health → dns
    from .health import check_dnsmasq_alive

    time.sleep(DNS_HEALTH_DELAY)
    if check_dnsmasq_alive():
        return

    opslog.warn("dnsmasq unreachable after nftables apply, restarting")
    run(["systemctl", "restart", DNSMASQ_SERVICE])
    time.sleep(DNS_HEALTH_DELAY)

    if not check_dnsmasq_alive():
        raise RuntimeError("DNS resolver (dnsmasq) is not responding")


def validate(mode: str) -> list[str]:
    """Real pre-deploy validation (audit H8): nft -c -f on the deployed config
    + dnsmasq --test on the generated DNS config. Returns error strings (empty
    on success). Consumed by the CLI `validate` command."""
    errors: list[str] = []
    try:
        run(["nft", "-c", "-f", NFTABLES_CONF], check=True)
    except Exception as e:  # noqa: BLE001 — surface all failure modes
        errors.append(f"nft -c -f failed: {e}")
    from .dns import _build_config
    from .guards import check_allowlist_nonempty

    rendered = _build_config(mode)
    # Write a test copy so we don't clobber the live config with an invalid one
    test_conf = Path("/tmp/allowlist-test.conf")
    test_conf.write_text(rendered)
    try:
        run(["dnsmasq", "--test", "--conf-file=" + str(test_conf)], check=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"dnsmasq --test failed: {e}")
    finally:
        test_conf.unlink(missing_ok=True)
    if not check_allowlist_nonempty():
        errors.append("allowlist is empty — locked mode would block everything")
    return errors
