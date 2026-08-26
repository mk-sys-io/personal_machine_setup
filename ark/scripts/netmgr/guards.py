from __future__ import annotations

import json
import os
import re
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
    """Verify DNS, TCP, TLE metadata, and the openssl/chpasswd tool gates.

    Returns the tle binary path on success. Raises NetworkError on failure.
    Absorbs the openssl/chpasswd binary gates so `ark enable` keeps its
    precondition set (audit H2).
    """
    tle = find_tle()
    _check_dns_resolution(DRAND_HOST)
    _check_tcp_connectivity(DRAND_HOST)
    _check_tle_metadata(tle)
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
    """Fail-closed preflight: the executed entrypoint + the scripts tree exist.

    The scripts dir is an import tree (invoked via python3/import, 0644) — the
    sudoers-gated entrypoint /usr/local/bin/netmgr is what must be executable;
    scripts/netmgr.py only needs to be present.
    """
    netmgr_bin = Path("{{ .Env.ARK_BIN_PATH }}/netmgr")
    if not os.access(netmgr_bin, os.X_OK):
        raise PrereqError(f"netmgr bin not executable: {netmgr_bin}")
    netmgr = Path(ARK_DATA) / "scripts" / "netmgr.py"
    if not netmgr.is_file():
        raise PrereqError(f"netmgr.py not deployed: {netmgr}")


def check_blocklist_dnsmasq() -> None:
    """Fail-closed: blocklist.dnsmasq.conf must exist before enable.

    Never auto-generates at the point of no return — it is a prep artifact,
    not system state (ark-enable-flow §1 step 4).
    """
    path = Path(ARK_DATA) / "domains" / "focused" / "blocklist.dnsmasq.conf"
    if not path.is_file():
        raise PrereqError(
            f"blocklist.dnsmasq.conf not found: {path}\n"
            "  Run: netmgr generate"
        )


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


# ── VPN Artifact Gate ─────────────────────────────────────────────────────────

VPN_PACKAGES: set[str] = {
    "wireguard-tools", "openvpn", "network-manager-openvpn",
    "strongswan", "openconnect", "vpnc", "pptp-linux", "xl2tpd",
    "tailscale", "nordvpn", "mullvad-vpn", "protonvpn", "expressvpn",
    "windscribe", "surfshark", "cyberghost", "ipvanish",
    "privateinternetaccess", "proton-vpn", "tunnelbear",
}

VPN_BINARIES: set[str] = {
    "wg", "wg-quick", "openvpn", "openconnect",
    "tailscale", "tailscaled", "ss-local", "sing-box",
    "xray", "v2ray", "v2raya", "hysteria", "gost",
    "trojan", "trojan-go", "naive", "tor", "torsocks",
}

VPN_UNITS_RE = re.compile(
    r"^(wg-quick@|openvpn@|tailscaled|shadowsocks|v2ray|xray|sing-box|hysteria|tor)\S*\.service$"
)

VPN_FILE_RE = re.compile(r"(vpn|wireguard|openvpn)", re.IGNORECASE)

USER_BIN_DIRS = [
    Path.home() / d for d in (".local/bin", "go/bin", ".cargo/bin", "bin")
]

_DPKG_QUERY = ["dpkg-query", "-W", "-f", chr(36) + "{Package}\n"]


def audit_vpn_evidence() -> None:
    """Gate: refuse ark enable if VPN artifacts are present on the system."""
    found: list[str] = []

    # 1. dpkg packages
    try:
        result = subprocess.run(
            _DPKG_QUERY,
            capture_output=True, text=True, timeout=10,
        )
        installed = set(result.stdout.splitlines())
        hits = installed & VPN_PACKAGES
        found += [f"package: {p}" for p in sorted(hits)]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # 2. binaries (PATH + user dirs)
    for name in VPN_BINARIES:
        if shutil.which(name):
            found.append(f"binary: {name} (PATH)")
    for d in USER_BIN_DIRS:
        if d.is_dir():
            for name in VPN_BINARIES:
                if (d / name).exists():
                    found.append(f"binary: {d / name}")

    # 3. systemd units
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", "--type=service", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            unit = line.split()[0]
            if VPN_UNITS_RE.match(unit):
                found.append(f"service: {unit}")
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # 4. stray files in ~/Downloads, ~/bin, /opt
    scan_dirs = [Path.home() / "Downloads", Path.home() / "bin", Path("/opt")]
    for d in scan_dirs:
        if not d.is_dir():
            continue
        try:
            for entry in d.iterdir():
                if entry.is_file() and VPN_FILE_RE.search(entry.name):
                    found.append(f"file: {entry}")
        except PermissionError:
            pass

    if found:
        remediation = "\n".join(f"  {item}" for item in found)
        raise PrereqError(
            f"VPN artifacts detected — remove before enabling:\n{remediation}"
        )


# ── Active Tunnel Gate ───────────────────────────────────────────────────────

def audit_active_tunnels() -> None:
    """Gate: refuse ark enable if tun/tap/wg interfaces are active."""
    found: list[str] = []

    # 1. tun/tap — detect by tun_flags file presence (type-based, not name)
    net_dir = Path("/sys/class/net")
    if net_dir.is_dir():
        for iface in net_dir.iterdir():
            if (iface / "tun_flags").exists():
                found.append(f"interface: {iface.name} (tun/tap)")

    # 2. wireguard — detect by interface type
    try:
        result = subprocess.run(
            ["ip", "-d", "-j", "link", "show", "type", "wireguard"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() not in ("", "[]"):
            for link in json.loads(result.stdout):
                name = link.get("ifname", "wg?")
                found.append(f"interface: {name} (wireguard)")
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        pass

    if found:
        remediation = "\n".join(f"  {item}" for item in found)
        raise PrereqError(
            f"active tunnel interfaces detected — bring down before enabling:\n"
            f"{remediation}\n"
            "  Use: ip link delete <interface> or stop the VPN service"
        )


# ── Package Drift Gate ──────────────────────────────────────────────────────

_BASELINE_DIR = Path(ARK_DATA) / "baselines"
_BASELINE_PKG_FILE = _BASELINE_DIR / "installed-packages.txt"

def capture_package_baseline() -> None:
    """Snapshot current dpkg state as the drift baseline."""
    try:
        result = subprocess.run(
            _DPKG_QUERY,
            capture_output=True, text=True, timeout=15,
        )
        packages = sorted(result.stdout.splitlines())
    except (subprocess.SubprocessError, FileNotFoundError):
        return
    _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _BASELINE_PKG_FILE.with_suffix(".tmp")
    try:
        tmp.write_text("\n".join(packages) + "\n")
        os.replace(tmp, _BASELINE_PKG_FILE)
    except OSError:
        pass


def audit_package_manifest_drift() -> None:
    """Gate: refuse ark enable if any package was added since last baseline."""
    if not _BASELINE_PKG_FILE.is_file():
        return
    try:
        baseline = set(_BASELINE_PKG_FILE.read_text().splitlines())
        result = subprocess.run(
            _DPKG_QUERY,
            capture_output=True, text=True, timeout=15,
        )
        current = set(result.stdout.splitlines())
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return
    added = sorted(current - baseline)
    if added:
        remediation = "\n".join(f"  sudo apt purge {p}" for p in added)
        raise PrereqError(
            f"package drift detected — packages installed since last baseline:\n"
            f"{remediation}"
        )


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


def require_openssl() -> None:
    if not shutil.which("openssl"):
        raise PrereqError("openssl not found")


def require_chpasswd() -> None:
    if not shutil.which("chpasswd"):
        raise PrereqError("chpasswd not found")


def require_unrestricted() -> None:
    """Gate allowlist edits to unrestricted mode exclusively.

    Mode file is 644/world-readable, so the gate is robust and works before
    sudo. Focused is blocked even with password sudo; locked has no sudo at
    all. Mode transitions are ark-only (`ark enable` / `lock` / `disable`) —
    `mode.write()` is the only correct write path.
    """
    from mode import read

    if read() != "unrestricted":
        raise PrereqError(
            "allowlist edits require unrestricted mode — run 'ark disable' first"
        )
