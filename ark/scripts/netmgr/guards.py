from __future__ import annotations

import os
import pwd
import shutil
import socket
import subprocess
import time
from pathlib import Path

import opslog

from ._util import run

# Gomplate-templated constants
ARK_DATA = "{{ .Env.ARK_DATA_PATH }}"
USERNAME = "{{ .Env.USERNAME }}"
DRAND_HOST = "{{ .Env.DRAND_HOST }}"
TLE_PRIMARY = "{{ .Env.TLE_PRIMARY_PATH }}"
TLE_FALLBACK = "{{ .Env.TLE_FALLBACK_PATH }}"


class NetworkError(Exception):
    """Raised when a network prerequisite fails."""


class PrereqError(Exception):
    """Raised when a non-network prerequisite fails."""


# ── Network checks ────────────────────────────────────────────────────────────

def check_prereqs() -> str:
    """Verify DNS, TCP, TLE metadata, and the system-credential tool gates.

    Returns the tle binary path on success. Raises NetworkError on failure.
    Absorbs `cask_system.check_cask_prereqs()` (183-192): adds the
    system.credentials / openssl / chpasswd gates so `ark enable` keeps its
    full precondition set (audit H2).
    """
    tle = find_tle()
    _check_dns_resolution(DRAND_HOST)
    _check_tcp_connectivity(DRAND_HOST)
    _check_tle_metadata(tle)
    check_system_cred()
    require_openssl()
    require_chpasswd()
    return str(tle)


def _check_dns_resolution(host: str, *, retries: int = 2, delay: int = 3) -> None:
    for attempt in range(retries):
        try:
            run(["timeout", "5", "getent", "hosts", host], timeout=10)
            return
        except (subprocess.SubprocessError, OSError):
            if attempt < retries - 1:
                opslog.info("DNS check failed (attempt %d/%d), retrying in %ds",
                            attempt + 1, retries, delay)
                time.sleep(delay)
    raise NetworkError(f"cannot resolve {host} (DNS failed)")


def _check_tcp_connectivity(host: str, port: int = 443) -> None:
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
    except (TimeoutError, OSError):
        raise NetworkError(f"cannot reach {host}:{port} (TCP failed)") from None


def _check_tle_metadata(tle_bin: str) -> None:
    try:
        r = run([tle_bin, "--metadata"], timeout=30)
        if "chain_hash" not in r.stdout:
            raise NetworkError("tle cannot reach drand timelock network")
    except subprocess.CalledProcessError:
        raise NetworkError("tle --metadata failed — cannot reach drand") from None


# ── File/prerequisite checks ──────────────────────────────────────────────────

def check_lockdown_dir() -> None:
    data = Path(ARK_DATA)
    if not data.is_dir():
        raise PrereqError(f"lockdown data directory not found: {data}")
    for name in ["scripts", "nftables.conf.base", "nftables.conf.restricted"]:
        if not (data / name).exists():
            raise PrereqError(f"missing required file: {data / name}")


def check_scripts() -> None:
    netmgr = Path(ARK_DATA) / "scripts" / "netmgr.py"
    if not os.access(netmgr, os.X_OK):
        raise PrereqError(f"netmgr.py not executable: {netmgr}")


def check_blocklist() -> None:
    path = Path(ARK_DATA) / "domains" / "blocklist.dnsmasq.conf"
    if not path.is_file():
        raise PrereqError(f"blocklist not found: {path}")


def audit_package_managers() -> None:
    blockers = ["flatpak", "snap", "nix"]
    found = [name for name in blockers if shutil.which(name)]
    extra_paths = [
        "/snap/bin/flatpak",
        "/snap/bin/snap",
        "/nix/var/nix/profiles/default/bin/nix",
    ]
    found += [p for p in extra_paths if os.path.isfile(p)]
    if found:
        raise PrereqError(f"conflicting package managers detected: {', '.join(found)}")


def _count_domains(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
    return count


def check_allowlist_nonempty() -> None:
    locked_dir = Path(ARK_DATA) / "domains" / "locked"
    for name in ["infra.txt", "base.txt", "session.txt"]:
        path = locked_dir / name
        if path.exists() and _count_domains(path) > 0:
            return
    raise PrereqError("allowlist is empty — add domains to infra.txt/base.txt/session.txt")


def find_tle() -> str:
    for path in [TLE_PRIMARY, TLE_FALLBACK]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise PrereqError(
        "tle binary not found\n"
        "  Install: go install github.com/drand/tle/cmd/tle@latest"
    )


# ── Tool checks ───────────────────────────────────────────────────────────────

def require_root() -> None:
    if os.geteuid() != 0:
        raise PrereqError("must run as root")


def require_chattr() -> None:
    if not shutil.which("chattr"):
        raise PrereqError("chattr not found")


def require_polars() -> None:
    try:
        import polars  # noqa: F401
    except ImportError:
        raise PrereqError("polars not installed: pip install polars") from None


# ── System-credential gates (absorbed from cask_system.check_cask_prereqs) ───

def check_system_cred() -> None:
    """Verify the system credentials file exists (mirrors cask_lib CASK_WORK_DIR)."""
    home = pwd.getpwnam(USERNAME).pw_dir
    cred_path = os.path.join(home, ".local", "share", "cask", "system.credentials")
    if not os.path.isfile(cred_path):
        raise PrereqError(
            f"{cred_path} not found\n"
            "  Run 'ark enable' or create the credentials file"
        )


def require_openssl() -> None:
    if not shutil.which("openssl"):
        raise PrereqError("openssl not found")


def require_chpasswd() -> None:
    if not shutil.which("chpasswd"):
        raise PrereqError("chpasswd not found")
