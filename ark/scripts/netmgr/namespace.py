"""Network namespace management — creates isolated network for bypass.

Replaces the legacy shell generator (namespace creation, veth, routing,
keepalive) and the old command gateway (absorbed as the `exec` subcommand).

Uses pyroute2 for netlink-based netns/veth/route management. sysctl and
`ip netns exec` remain as subprocess calls (pyroute2 cannot do those).
Fix #1: every netns-side operation goes through the NetNS handle — a bare
IPRoute() inside would open a host-namespace socket and silently act on the
host (e.g. the default-route del in _configure_routing could delete the HOST
default route). Fix #5: exec/run gate on the data-driven allowlist.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import opslog
from pyroute2 import IPRoute, NetlinkError, NetNS, netns

from . import wrappers
from .guards import require_root, require_unrestricted

# Gomplate-templated constants
NETNS_NAME = "{{ .Env.NETNS_NAME }}"
NETNS_HOST = "{{ .Env.NETNS_HOST }}"
NETNS_CLIENT = "{{ .Env.NETNS_CLIENT }}"
NETNS_VETH_HOST = "{{ .Env.NETNS_VETH_HOST }}"
NETNS_VETH_NS = "{{ .Env.NETNS_VETH_NS }}"
DNS_PRIMARY = "{{ .Env.DNS_PRIMARY }}"
KEEPALIVE_TIME = int("{{ .Env.NETNS_KEEPALIVE_TIME }}")
KEEPALIVE_INTVL = int("{{ .Env.NETNS_KEEPALIVE_INTVL }}")
KEEPALIVE_PROBES = int("{{ .Env.NETNS_KEEPALIVE_PROBES }}")
NETNS_EXEC_ALLOWLIST = "{{ .Env.ARK_DATA_PATH }}/netns-exec-allowlist.txt"


class NamespaceError(Exception):
    """Raised when namespace operations fail."""


def start() -> None:
    """Create namespace, veth pair, routing, TCP keepalive.

    Idempotent: safe to run multiple times.
    """
    _cleanup_stale()
    _create_namespace()
    _create_veth_pair()
    _configure_routing()
    _verify_routing()
    _set_keepalive()
    opslog.info("namespace %s started", NETNS_NAME)


def stop() -> None:
    """Destroy namespace."""
    try:
        netns.remove(NETNS_NAME)
        opslog.info("namespace %s stopped", NETNS_NAME)
    except OSError as e:
        opslog.warn("failed to stop namespace: %s", e)


def status() -> dict:
    """Check namespace, veth, routing. Returns status dict."""
    ns_exists = NETNS_NAME in netns.listnetns()
    veth_exists = _veth_exists()
    routing_ok = _verify_routing_silent() if ns_exists else False
    return {
        "namespace": ns_exists,
        "veth": veth_exists,
        "routing": routing_ok,
        "name": NETNS_NAME,
    }


def exec_cmd(cmd: list[str]) -> None:
    """Run a command inside the namespace as the invoking user (audit C2).

    Namespace is set up via pyroute2, but executing inside must go through
    `ip netns exec … sudo -u $SUDO_USER` so the command runs with the user's
    identity/context — a bare pyroute2 `NetNS` context would run as root with
    no home/XDG. Guard on SUDO_USER so we fail loudly rather than run as root.
    """
    _validate_command(cmd)
    user = os.getenv("SUDO_USER")
    if not user:
        raise NamespaceError("exec requires sudo (SUDO_USER unset)")
    argv = ["ip", "netns", "exec", NETNS_NAME, "sudo", "-u", user, *cmd]
    subprocess.run(argv, check=True)


def run_cmd(
    cmd: list[str] | None = None,
    *,
    show_list: bool = False,
    show_status: bool = False,
) -> None:
    """Run a command in the correct network context for the current mode.

    User-space (no sudo) — absorbs the mode-aware logic that the per-app
    wrappers (tools/opencode.sh) used to duplicate. `/usr/local/bin/inet`
    is a short shim for this. The sudoers-gated `exec` stays the ONLY path
    that touches the namespace, so in non-locked modes nothing escalates.

    Empty argv or `--list` prints the grants table; `--status` prints
    mode · service · grants.
    """
    if show_status:
        _show_status()
        return
    if show_list or not cmd:
        list_grants()
        return

    # 1. Namespace service active → escalate to the sudoers-gated primitive
    if subprocess.run(
        ["systemctl", "is-active", "--quiet", NETNS_NAME], check=False
    ).returncode == 0:
        subprocess.run(["sudo", "netmgr", "namespace", "exec", *cmd], check=True)
        return

    # 2. Namespace down + non-locked mode → run directly as the invoking user
    mode_path = Path("/opt/ark/mode")
    mode = mode_path.read_text().strip() if mode_path.exists() else "unrestricted"
    if mode != "locked":
        sys.stderr.write(
            "Warning: internet-netns service is not running — running "
            "without network namespace\n"
        )
        subprocess.run(cmd, check=True)
        return

    # 3. Namespace down + locked → namespace is required (parity with the old
    #    wrapper: interactive prompt, non-interactive abort)
    if sys.stdin.isatty():
        sys.stderr.write(
            "Warning: internet-netns service is not running (locked mode)\n"
        )
        sys.stderr.write(
            "opencode will run without network namespace (internet access blocked)\n"
        )
        answer = input("Continue without namespace? [y/N]: ")
        if answer.strip().lower().startswith("y"):
            subprocess.run(cmd, check=True)
            return
        raise NamespaceError("aborted — namespace required in locked mode")

    raise NamespaceError(
        "internet-netns service is not running (locked mode)\n"
        "Start the namespace: sudo systemctl start internet-netns"
    )


def list_grants() -> None:
    """Print the grants table (shim path for thin grants, alias for dispatch)."""
    grants = wrappers.read_grants()
    if not grants:
        print("No grants configured")
        return
    header = ("Grant", "Ergonomics", "Realpath", "Status")
    rows: list[tuple[str, str, str, str]] = []
    for g in grants:
        name = g.path if g.arg is None else f"{g.path} {g.arg}"
        if g.arg is None:
            ergo = f"{wrappers.SHIM_DIR}/{os.path.basename(g.realpath)}"
        else:
            ergo = f"alias: {os.path.basename(g.realpath)}-{g.arg}"
        status = "MISSING" if g.missing else ""
        rows.append((name, ergo, g.realpath, status))
    widths = [max(len(row[i]) for row in rows + [header]) for i in range(len(header))]
    print("  ".join(col.ljust(widths[i]) for i, col in enumerate(header)))
    for row in rows:
        print("  ".join(col.ljust(widths[i]) for i, col in enumerate(row)))


def add_grant(binary: str, arg: str | None = None) -> None:
    """Grant a binary in the namespace allowlist (unrestricted + root only).

    Resolves the binary (see wrappers.resolve_binary), appends `path [arg]`
    to the deployed allowlist atomically (root:root 0644), regenerates shims,
    and prints the line to sync into etc/ark/netns-exec-allowlist.txt.
    """
    require_unrestricted()
    require_root()
    if arg is not None:
        arg = arg.strip()
        if not arg or any(ch.isspace() for ch in arg):
            raise NamespaceError("arg must be a single token (no whitespace)")
    realpath = wrappers.resolve_binary(binary)
    for g in wrappers.read_grants():
        if g.realpath == realpath and g.arg == arg:
            opslog.info("Already granted: %s", _fmt_grant(realpath, arg))
            return
    lines = _read_allowlist()
    lines.append(f"{_fmt_grant(realpath, arg)}\n")
    _write_allowlist(lines)
    wrappers.deploy()
    opslog.info("Granted: %s", _fmt_grant(realpath, arg))
    print(
        "Sync to etc/ark/netns-exec-allowlist.txt: "
        f"{_fmt_grant(realpath, arg)}"
    )


def remove_grant(binary: str, arg: str | None = None) -> None:
    """Revoke a binary grant from the namespace allowlist (unrestricted + root).

    Matches on realpath + optional arg; tolerates a binary that no longer
    resolves (falls back to the raw path) so stale entries stay removable.
    """
    require_unrestricted()
    require_root()
    if arg is not None:
        arg = arg.strip()
        if not arg or any(ch.isspace() for ch in arg):
            raise NamespaceError("arg must be a single token (no whitespace)")
    try:
        realpath = wrappers.resolve_binary(binary)
    except wrappers.WrapperError:
        realpath = os.path.realpath(binary) if os.path.exists(binary) else binary
    kept: list[str] = []
    removed: list[str] = []
    for line in _read_allowlist():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        parts = stripped.split()
        entry_path = (
            os.path.realpath(parts[0]) if os.path.exists(parts[0]) else parts[0]
        )
        entry_arg = parts[1] if len(parts) > 1 else None
        if entry_path == realpath and entry_arg == arg:
            removed.append(stripped)
        else:
            kept.append(line)
    if not removed:
        opslog.info("No matching grant to remove: %s", _fmt_grant(realpath, arg))
        return
    _write_allowlist(kept)
    wrappers.deploy()
    for entry in removed:
        opslog.info("Revoked: %s", entry)


def _show_status() -> None:
    """Print mode · service · grants for `inet --status`."""
    from mode import read

    mode = read()
    service_running = (
        subprocess.run(
            ["systemctl", "is-active", "--quiet", NETNS_NAME], check=False
        ).returncode
        == 0
    )
    grants = wrappers.read_grants()
    names = [
        os.path.basename(g.realpath)
        if g.arg is None
        else f"{os.path.basename(g.realpath)}-{g.arg}"
        for g in grants
    ]
    print(f"Mode: {mode}")
    print(f"Service: {NETNS_NAME} ({'running' if service_running else 'stopped'})")
    summary = f"Grants: {len(grants)}"
    if names:
        summary += f" ({', '.join(names)})"
    print(summary)


def _fmt_grant(realpath: str, arg: str | None) -> str:
    """Serialize a grant as its allowlist line (path or `path arg`)."""
    return realpath if arg is None else f"{realpath} {arg}"


def _read_allowlist() -> list[str]:
    """Read deployed allowlist lines; empty list when absent."""
    try:
        with open(NETNS_EXEC_ALLOWLIST) as f:
            return f.readlines()
    except OSError:
        return []


def _write_allowlist(lines: list[str]) -> None:
    """Atomic write preserving root:root 0644 (readable list/alias gen)."""
    target = Path(NETNS_EXEC_ALLOWLIST)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text("".join(lines))
    tmp.rename(target)
    os.chmod(target, 0o644)
    os.chown(target, 0, 0)


def _cleanup_stale() -> None:
    """Remove stale namespace/veth from unclean shutdown."""
    ns_list = netns.listnetns()
    ns_exists = NETNS_NAME in ns_list
    veth_exists = _veth_exists()

    if ns_exists and not veth_exists:
        opslog.warn("stale namespace %s (no veth) — removing", NETNS_NAME)
        netns.remove(NETNS_NAME)

    if veth_exists and not ns_exists:
        opslog.warn("stale veth %s (no namespace) — removing", NETNS_VETH_HOST)
        with IPRoute() as ipr:
            links = list(ipr.link("dump", ifname=NETNS_VETH_HOST))
            if links:
                ipr.link("del", index=links[0]["index"])


def _create_namespace() -> None:
    if NETNS_NAME not in netns.listnetns():
        netns.create(NETNS_NAME)
        opslog.info("created namespace %s", NETNS_NAME)


def _create_veth_pair() -> None:
    with IPRoute() as ipr:
        existing = list(ipr.link("dump", ifname=NETNS_VETH_HOST))
        if not existing:
            ipr.link(
                "add",
                ifname=NETNS_VETH_HOST,
                kind="veth",
                peer={"ifname": NETNS_VETH_NS},
            )
            opslog.info("created veth pair %s <-> %s", NETNS_VETH_HOST, NETNS_VETH_NS)

        # Move peer into namespace
        peer = list(ipr.link("dump", ifname=NETNS_VETH_NS))
        if peer:
            ipr.link("set", index=peer[0]["index"], net_ns_fd=NETNS_NAME)

        # Assign host IP (ignore EEXIST — idempotent across re-runs)
        host = list(ipr.link("dump", ifname=NETNS_VETH_HOST))
        if host:
            try:
                ipr.addr("add", index=host[0]["index"], address=NETNS_HOST, prefixlen=30)
            except NetlinkError as e:
                if e.code != 17:  # EEXIST
                    raise

        # Bring up host veth
        ipr.link("set", index=host[0]["index"], state="up")

    # Configure namespace side — use the NetNS handle directly; a bare
    # IPRoute() here would open a host-namespace socket (fix #1)
    with NetNS(NETNS_NAME) as ns:
        peer_ns = list(ns.link("dump", ifname=NETNS_VETH_NS))
        if peer_ns:
            try:
                ns.addr("add", index=peer_ns[0]["index"], address=NETNS_CLIENT, prefixlen=30)
            except NetlinkError as e:
                if e.code != 17:  # EEXIST
                    raise
            ns.link("set", index=peer_ns[0]["index"], state="up")

        # Bring up loopback
        lo = list(ns.link("dump", ifname="lo"))
        if lo:
            ns.link("set", index=lo[0]["index"], state="up")


def _configure_routing() -> None:
    with NetNS(NETNS_NAME) as ns:  # ns IS the namespace-bound handle (fix #1)
        # Delete default route by dst+table (idempotent; ESRCH = none present)
        try:
            ns.route("del", {"dst": "0.0.0.0/0", "table": 254})
        except NetlinkError as e:
            if e.code != 3:  # ESRCH
                raise

        # Add default route via host, pinned to the veth's oif so the next-hop
        # is reachable (gateway alone is ambiguous if the link is not the
        # default-route carrier)
        host_if = list(ns.link("dump", ifname=NETNS_VETH_NS))
        ns.route(
            "add",
            {"dst": "0.0.0.0/0", "gateway": NETNS_HOST, "oif": host_if[0]["index"]},
        )


def _verify_routing() -> None:
    with NetNS(NETNS_NAME) as ns:  # ns IS the namespace-bound handle (fix #1)
        result = list(ns.route("get", {"dst": DNS_PRIMARY}))

        if not result:
            raise NamespaceError("routing check failed — no route to upstream via veth")


def _verify_routing_silent() -> bool:
    try:
        _verify_routing()
        return True
    except (NamespaceError, OSError):
        return False


def _set_keepalive() -> None:
    """TCP keepalive — prevent CDN idle timeout for long-lived connections."""
    for key, val in [
        ("net.ipv4.tcp_keepalive_time", KEEPALIVE_TIME),
        ("net.ipv4.tcp_keepalive_intvl", KEEPALIVE_INTVL),
        ("net.ipv4.tcp_keepalive_probes", KEEPALIVE_PROBES),
    ]:
        subprocess.run(
            ["ip", "netns", "exec", NETNS_NAME, "sysctl", "-qw", f"{key}={val}"],
            check=True,
        )


def _veth_exists() -> bool:
    with IPRoute() as ipr:
        links = list(ipr.link("dump", ifname=NETNS_VETH_HOST))
        return len(links) > 0


def _validate_command(cmd: list[str]) -> None:
    """Validate command against the data-driven allowlist (fix #5).

    Reads `binary [arg]` lines from $ARK_DATA_PATH/netns-exec-allowlist.txt
    (gomplated + deployed by 60-ark.sh; source etc/ark/netns-exec-allowlist.txt).
    A trailing `arg` on a line restricts that binary to that first arg (keeps
    the old `podman pull`-only rule); a bare `binary` matches any args (e.g.
    opencode). Adding an approved binary = one-line edit to the source file.
    sudoers stays `netmgr namespace exec *` (audit C2) — netmgr enforces the
    allowlist internally.
    """
    if not cmd:
        raise NamespaceError("no command specified")

    bin_path = cmd[0]
    resolved = os.path.realpath(bin_path) if os.path.exists(bin_path) else bin_path

    for raw in Path(NETNS_EXEC_ALLOWLIST).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        entry_path = os.path.realpath(parts[0]) if os.path.exists(parts[0]) else parts[0]
        entry_arg = parts[1] if len(parts) > 1 else None

        if resolved != entry_path:
            continue
        if entry_arg is None or (len(cmd) >= 2 and cmd[1] == entry_arg):
            return

    raise NamespaceError(
        f"command not permitted in namespace: {' '.join(cmd)}\n"
        "Allowed: see $ARK_DATA_PATH/netns-exec-allowlist.txt"
    )
