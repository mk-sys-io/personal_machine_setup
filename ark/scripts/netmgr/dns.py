from __future__ import annotations

from pathlib import Path

import opslog

from ._util import run

# Gomplate-templated constants
# DNSMASQ_CONF is a literal path — the value the deployed dnsmasq
# configs/relatives expect (the old shell generator wrote it too). The
# template var DNSMASQ_CONF_PATH is dropped.
DNSMASQ_CONF = "/etc/dnsmasq.d/allowlist.conf"
UPSTREAM_V4 = "{{ .Env.DNS_PRIMARY }}"
UPSTREAM_V6 = "{{ .Env.DNS_SECONDARY }}"
LISTEN_ADDR = "{{ .Env.DNSMASQ_LISTEN_ADDR }}"
LISTEN_PORT = "{{ .Env.DNSMASQ_LISTEN_PORT }}"
BLOCKLIST_PATH = "{{ .Env.ARK_DATA_PATH }}/domains/focused/blocklist.dnsmasq.conf"
DNSMASQ_SERVICE = "{{ .Env.DNSMASQ_SERVICE }}"
ARK_DATA = "{{ .Env.ARK_DATA_PATH }}"


def configure(mode: str) -> None:
    """Write dnsmasq config for the given mode and restart the service.

    Modes:
      unrestricted — forward all queries upstream, no blocking
      focused      — forward upstream, block distraction domains via conf-file
      locked       — only allowlisted domains resolve, everything else NXDOMAIN
    """
    content = _build_config(mode)
    _write_config(content, DNSMASQ_CONF)
    _restart()
    opslog.info("dnsmasq configured for %s mode", mode)


def _build_config(mode: str) -> str:
    """Generate the dnsmasq config file content."""
    shared = [
        "no-resolv",
        "bind-interfaces",
        f"listen-address={LISTEN_ADDR}",
        f"port={LISTEN_PORT}",
        "domain-needed",
        "bogus-priv",
    ]

    if mode == "unrestricted":
        return "\n".join(shared + [f"server={UPSTREAM_V4}", f"server={UPSTREAM_V6}"]) + "\n"

    if mode == "focused":
        shared.append(f"conf-file={BLOCKLIST_PATH}")
        return "\n".join(shared + [f"server={UPSTREAM_V4}", f"server={UPSTREAM_V6}"]) + "\n"

    if mode == "locked":
        # Audit C1: no global server= in locked mode — a global upstream would
        # resolve every non-allowlisted domain and the dnsmasq_local query would
        # be meaningless. Only per-domain server=/domain/ rules from the
        # allowlist files appear here.
        _cleanup_stale()
        allowlist = _build_allowlist()
        deny = _build_deny()
        return "\n".join(shared + allowlist + deny) + "\n"

    raise ValueError(f"Unknown mode: {mode}")


def _parse_domains(path: Path) -> list[str]:
    """Read domains from a list file: strip inline comments and `*.` wildcard
    prefixes (parity with the old generator and policies.py)."""
    if not path.is_file():
        return []
    domains: list[str] = []
    for line in path.read_text().splitlines():
        domain = line.split("#", 1)[0].strip()
        if not domain:
            continue
        domain = domain.lstrip("*.")
        if domain:
            domains.append(domain)
    return domains


def _build_allowlist() -> list[str]:
    """Per-domain server rules from locked/{infra,base,session}.txt (deduped, sorted)."""
    rules: list[str] = []
    for name in ("infra", "base", "session"):
        src = Path(ARK_DATA) / "domains" / "locked" / f"{name}.txt"
        for domain in _parse_domains(src):
            rules.append(f"server=/{domain}/{UPSTREAM_V4}")
            rules.append(f"server=/{domain}/{UPSTREAM_V6}")
    return sorted(set(rules))


def _build_deny() -> list[str]:
    """deny address=/domain/ lines for the deny list (blocked-forced-NXDOMAIN).

    Fix #2: reads domains/locked/deny.txt — deny.txt was MOVED into locked/
    (P1); the old domains/deny.txt path never exists, so locked config would
    otherwise emit no deny lines.
    """
    src = Path(ARK_DATA) / "domains" / "locked" / "deny.txt"
    return [f"address=/{domain}/" for domain in _parse_domains(src)]


def _cleanup_stale() -> None:
    """Remove legacy 00-initial.conf / 99-allowlist.conf written by the old
    generator — superseded by one file."""
    for stale in ("00-initial.conf", "99-allowlist.conf"):
        p = Path("/etc/dnsmasq.d") / stale
        if p.exists():
            p.unlink()
            opslog.info("removed stale %s", p)


def _restart() -> None:
    """Restart dnsmasq and verify it started."""
    run(["systemctl", "restart", DNSMASQ_SERVICE])
    # Function-level import to avoid circular dependency: dns → health → dns
    from .health import check_dnsmasq_alive

    if not check_dnsmasq_alive():
        opslog.error("dnsmasq failed to start after restart")
        raise RuntimeError("dnsmasq failed to start")


def _write_config(content: str, path: str) -> None:
    """Atomically write config file."""
    target = Path(path)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.rename(target)
    target.chmod(0o644)
