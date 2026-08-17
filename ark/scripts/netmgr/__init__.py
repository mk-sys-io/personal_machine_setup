"""netmgr — network management for the lockdown system.

Full typer CLI. ``main()`` lives here so both ``netmgr.py`` (standalone
shim) and ``__main__.py`` (``python -m netmgr``) can import it. Blocklist
commands are flattened to top-level — ``netmgr generate`` not
``netmgr blocklist generate``.
"""
from __future__ import annotations

import json
import os
from typing import Annotated

import typer

from . import allowlist, firewall, guards, namespace, system, wrappers
from . import health as health_mod
from .dns import configure as configure_dns
from .firewall import apply as apply_firewall
from .policies import deploy as deploy_policies

app = typer.Typer(help="Network management for the lockdown system")
ns_app = typer.Typer(help="Network namespace management")
sys_app = typer.Typer(help="System DNS configuration")
al_app = typer.Typer(help="Allowlist management (read any mode, edit unrestricted)")
eg_app = typer.Typer(help="Namespace exec-grant management (edit unrestricted)")
src_app = typer.Typer(help="Source management (unrestricted only)")
ex_app = typer.Typer(help="Wildcard exception management (edit unrestricted)")
app.add_typer(ns_app, name="namespace")
app.add_typer(sys_app, name="system")
app.add_typer(al_app, name="allowlist")
app.add_typer(eg_app, name="exec-grant")
app.add_typer(src_app, name="sources")
app.add_typer(ex_app, name="exempt")


@app.command()
def configure(
    mode: Annotated[str, typer.Argument(help="Mode to configure")],
) -> None:
    """Configure DNS + firewall + policies."""
    configure_dns(mode)
    apply_firewall(mode)
    deploy_policies()


@app.command()
def generate() -> None:
    """Generate blocklist from all sources."""
    from .blocklist import generate as _generate

    _generate()


@app.command()
def download(
    urls: Annotated[list[str], typer.Argument(help="URL(s) to download")],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Accepted for compatibility (downloads always refresh)")
    ] = False,
    name: Annotated[str | None, typer.Option("--name", "-n")] = None,
) -> None:
    """Download upstream blocklist(s)."""
    from .blocklist import download as _download

    _download(urls=urls, force=force, name=name)


@app.command()
def add(
    domains: Annotated[list[str], typer.Argument(help="Domain(s) to add")],
    group: Annotated[
        str | None,
        typer.Option("--group", "-g", help="Target group banner (existing or new)"),
    ] = None,
    file: Annotated[
        str | None, typer.Option("--file", help="Bulk import domains from a file")
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Add domain(s) to custom blocklist (unrestricted only)."""
    guards.require_unrestricted()
    guards.require_root()
    from .blocklist import add as _add

    _add(domains=domains, group=group, file=file, yes=yes, dry_run=dry_run)


def _require_target(domain: str | None, group: str | None) -> None:
    """Validate a domain or --group was provided; gate edit to unrestricted."""
    if domain is None and group is None:
        typer.echo("Provide a domain or --group", err=True)
        raise typer.Exit(code=1)
    guards.require_unrestricted()
    guards.require_root()


@app.command()
def block(
    domain: Annotated[str | None, typer.Argument(help="Domain to block")] = None,
    group: Annotated[
        str | None, typer.Option("--group", "-g", help="Group/banner name")
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Uncomment a domain/group + remove from exclude -> blocked."""
    _require_target(domain, group)
    from .blocklist import block as _block

    _block(domain=domain, group=group, yes=yes, dry_run=dry_run)


@app.command()
def unblock(
    domain: Annotated[str | None, typer.Argument(help="Domain to unblock")] = None,
    group: Annotated[
        str | None, typer.Option("--group", "-g", help="Group/banner name")
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Comment out a domain/group + add to exclude -> allowed."""
    _require_target(domain, group)
    from .blocklist import unblock as _unblock

    _unblock(domain=domain, group=group, yes=yes, dry_run=dry_run)


@app.command()
def remove(
    domain: Annotated[str | None, typer.Argument(help="Domain to remove")] = None,
    group: Annotated[
        str | None, typer.Option("--group", "-g", help="Group/banner name")
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Delete a domain/group from custom + add to exclude."""
    _require_target(domain, group)
    from .blocklist import remove as _remove

    _remove(domain=domain, group=group, yes=yes, dry_run=dry_run)


@app.command()
def lookup(
    domain: Annotated[str, typer.Argument(help="Domain to look up")],
) -> None:
    """Show blocked/exempt status for a domain (exemption wins)."""
    from .blocklist import lookup as _lookup

    _lookup(domain=domain)


@app.command()
def search(
    pattern: Annotated[str, typer.Argument(help="Regex pattern to search for")],
) -> None:
    """Regex search across all blocklists."""
    from .blocklist import search as _search

    _search(pattern=pattern)


@app.command()
def purge(
    targets: Annotated[list[str] | None, typer.Argument(help="UUID(s) or position(s) to purge")] = None,
    all_domains: Annotated[bool, typer.Option("--all")] = False,
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
) -> None:
    """Remove downloaded blocklist(s)."""
    guards.require_unrestricted()
    guards.require_root()
    from .blocklist import purge as _purge

    _purge(all_domains=all_domains, targets=targets, yes=yes)


@app.command()
def stats() -> None:
    """Show domain counts per file."""
    from .blocklist import stats as _stats

    _stats()


@app.command()
def verify() -> None:
    """Verify blocklist file integrity."""
    from .blocklist import verify as _verify

    _verify()


# -- sources subcommands (edit unrestricted only) -----------------------------


@src_app.command("list")
def src_list() -> None:
    """List all sources from sources.json."""
    import opslog

    from .blocklist.manage import load_sources

    sources = load_sources()
    if not sources:
        opslog.info("No sources configured.")
        return

    opslog.info(
        "  %-6s %-40s %-30s %10s",
        "TYPE",
        "ID",
        "CATEGORIES",
        "STATUS",
    )
    opslog.info("  " + "-" * 90)
    for src in sources:
        stype = str(src.get("type", "user"))
        sid = str(src.get("id", ""))
        cats_raw = src.get("categories", [])
        cats = ", ".join(cats_raw) if isinstance(cats_raw, list) and cats_raw else "-"
        enabled = "ENABLED" if src.get("enabled") else "DISABLED"
        opslog.info("  [%-4s] %-40s %-30s %10s", stype, sid, cats, enabled)


@src_app.command("add")
def src_add(
    url: Annotated[str, typer.Argument(help="URL of the upstream blocklist")],
) -> None:
    """Add a new source URL (interactive category prompt)."""
    import opslog

    from .blocklist.download import _extract_source_id
    from .blocklist.manage import load_sources, save_sources

    guards.require_unrestricted()
    guards.require_root()

    sources = load_sources()

    # Check for duplicate URL
    normalized = url.strip().rstrip("/")
    for src in sources:
        if str(src.get("url", "")).rstrip("/") == normalized:
            opslog.info("Source already exists: %s", src.get("id"))
            return

    # Extract ID from URL
    source_id = _extract_source_id(url)

    # Prompt for categories
    opslog.info("Enter categories (comma-separated, e.g. ads,malware,porn):")
    cats_input = input("  > ").strip()
    categories = [c.strip().lower() for c in cats_input.split(",") if c.strip()]

    new_source = {
        "id": source_id,
        "url": url,
        "categories": categories,
        "type": "user",
        "enabled": True,
    }

    sources.append(new_source)
    save_sources(sources)
    opslog.info("Added source: %s (%s)", source_id, url)
    opslog.info("  Run 'netmgr sources update' to download.")


@src_app.command("toggle")
def src_toggle(
    source_id: Annotated[str, typer.Argument(help="Source ID to enable/disable")],
) -> None:
    """Toggle a source enabled/disabled."""
    import opslog

    from .blocklist.manage import load_sources, save_sources

    guards.require_unrestricted()
    guards.require_root()

    sources = load_sources()
    for src in sources:
        if src.get("id") == source_id:
            current = src.get("enabled", False)
            src["enabled"] = not current
            save_sources(sources)
            state = "enabled" if not current else "disabled"
            opslog.info("Source %s: %s", state, source_id)
            return

    opslog.info("Source not found: %s", source_id)


@src_app.command("update")
def src_update(
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Accepted for compatibility (downloads always refresh)")
    ] = False,
) -> None:
    """Re-download all enabled sources and regenerate blocklist."""
    from .blocklist import download as _download
    from .blocklist import generate as _generate

    guards.require_unrestricted()
    _download(force=force)
    _generate()


@src_app.command("download")
def src_download(
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Accepted for compatibility (downloads always refresh)")
    ] = False,
) -> None:
    """Download all enabled sources without regenerating the blocklist."""
    from .blocklist import download as _download

    guards.require_unrestricted()
    _download(force=force)


# -- exempt subcommands (edit unrestricted; list any mode) --------------------


@ex_app.command("add")
def ex_add(
    domain: Annotated[str, typer.Argument(help="Domain to exempt")],
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Add a wildcard exception (server=/domain/#) (unrestricted only)."""
    guards.require_unrestricted()
    guards.require_root()
    from .blocklist import exempt_add as _exempt_add

    _exempt_add(domain=domain, yes=yes, dry_run=dry_run)


@ex_app.command("remove")
def ex_remove(
    domain: Annotated[str, typer.Argument(help="Domain to un-exempt")],
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Remove a wildcard exception (unrestricted only)."""
    guards.require_unrestricted()
    guards.require_root()
    from .blocklist import exempt_remove as _exempt_remove

    _exempt_remove(domain=domain, yes=yes, dry_run=dry_run)


@ex_app.command("list")
def ex_list() -> None:
    """Show each exemption + the parent wildcard it overrides."""
    from .blocklist import exempt_list as _exempt_list

    _exempt_list()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Network management for the lockdown system."""
    import opslog

    level = "DEBUG" if verbose else "INFO"
    # file= takes a path string, never True (audit C5) — opslog appends to the
    # per-service log under ARK_DATA_PATH/logs/
    # Log to user-writable location — netmgr namespace run is user-space and
    # cannot write to /opt/ark/logs/ without root.  Other ark tools (ark,
    # mcask, uncask) run as root and keep logging to ARK_DATA_PATH/logs/.
    user_log = os.path.expanduser("~/.local/logs/netmgr.log")
    opslog.configure(
        "netmgr",
        file=user_log,
        terminal_level=level,
    )
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# ── namespace subcommands ──────────────────────────────────────────────────────


@ns_app.command("start")
def ns_start() -> None:
    """Create namespace + veth + routing."""
    namespace.start()


@ns_app.command("stop")
def ns_stop() -> None:
    """Destroy namespace."""
    namespace.stop()


@ns_app.command("status")
def ns_status() -> None:
    """Show namespace status."""
    typer.echo(json.dumps(namespace.status(), indent=2))


@ns_app.command("exec")
def ns_exec(
    cmd: Annotated[list[str], typer.Argument(help="Command to run in namespace")],
) -> None:
    """Run command in namespace."""
    namespace.exec_cmd(cmd)


@ns_app.command("run")
def ns_run(
    cmd: Annotated[
        list[str] | None,
        typer.Argument(help="Command to run in the right network context"),
    ] = None,
    list_grants: Annotated[bool, typer.Option("--list", help="Show grants table")] = False,
    status: Annotated[bool, typer.Option("--status", help="Show mode · service · grants")] = False,
) -> None:
    """Run a command in the correct network context for the current mode."""
    namespace.run_cmd(cmd or [], show_list=list_grants, show_status=status)


# ── exec-grant subcommands (edit unrestricted; deploy/list any mode) ─────────


@eg_app.command("add")
def eg_add(
    binary: Annotated[str, typer.Argument(help="Binary name or path to grant")],
) -> None:
    """Grant a binary in the namespace allowlist (unrestricted only)."""
    namespace.add_grant(binary)


@eg_app.command("remove")
def eg_remove(
    binary: Annotated[str, typer.Argument(help="Binary name or path to revoke")],
) -> None:
    """Revoke a binary grant from the namespace allowlist (unrestricted only)."""
    namespace.remove_grant(binary)


@eg_app.command("deploy")
def eg_deploy() -> None:
    """Rebuild thin shims + inet (root only)."""
    wrappers.deploy()


@eg_app.command("list")
def eg_list() -> None:
    """Show the grants table."""
    namespace.list_grants()


# ── system subcommands ─────────────────────────────────────────────────────────


@sys_app.command("setup-dns")
def sys_setup_dns() -> None:
    """Configure NM dns=none + resolv.conf."""
    system.setup_dns()


@sys_app.command("setup-podman-dns")
def sys_setup_podman_dns() -> None:
    """Configure container DNS."""
    system.setup_podman_dns()


# ── allowlist subcommands (read any mode; edit gated to unrestricted) ─────────


@al_app.command("list")
def al_list(
    section: Annotated[str | None, typer.Option("--section", "-s", help="Section to list")] = None,
) -> None:
    """List allowlist domains with per-section headers."""
    allowlist.list_sections(section)


@al_app.command("search")
def al_search(
    pattern: Annotated[str, typer.Argument(help="Pattern to search for")],
) -> None:
    """Search allowlist domains."""
    allowlist.search(pattern)


@al_app.command("add")
def al_add(
    section: Annotated[str, typer.Argument(help="Section (infra/base/session)")],
    domains: Annotated[list[str], typer.Argument(help="Domain(s) to add")],
) -> None:
    """Add domain(s) to an allowlist section (unrestricted only)."""
    allowlist.add(section, domains)


@al_app.command("remove")
def al_remove(
    section: Annotated[str, typer.Argument(help="Section (infra/base/session)")],
    domains: Annotated[list[str], typer.Argument(help="Domain(s) to remove")],
) -> None:
    """Remove domain(s) from an allowlist section (unrestricted only)."""
    allowlist.remove(section, domains)


@al_app.command("clear-session")
def al_clear_session() -> None:
    """Clear the session allowlist (unrestricted only)."""
    allowlist.clear_session()


# ── standalone commands ────────────────────────────────────────────────────────


@app.command()
def check() -> None:
    """Verify network prerequisites."""
    try:
        guards.check_prereqs()
        guards.check_lockdown_dir()
        guards.check_scripts()
        guards.check_blocklist_dnsmasq()
        typer.echo("All prerequisites met")
    except (guards.NetworkError, guards.PrereqError) as e:
        typer.echo(f"FAILED: {e}", err=True)
        raise typer.Exit(code=1) from None


@app.command("health")
def health_cmd(
    mode: Annotated[str | None, typer.Argument(help="Mode to check")] = None,
) -> None:
    """Post-apply health checks."""
    results = health_mod.check_all(mode)
    failed = [k for k, v in results.items() if not v]
    if failed:
        raise typer.Exit(code=1)


@app.command("status")
def status() -> None:
    """Show current network state."""
    from mode import read

    typer.echo(f"Mode: {read()}")
    typer.echo("dnsmasq: {{ .Env.DNSMASQ_LISTEN_ADDR }}:{{ .Env.DNSMASQ_LISTEN_PORT }}")
    c = allowlist.counts()
    typer.echo("Allowlist:")
    for name in ("infra", "base", "session"):
        typer.echo(f"  {name}: {c[name]}")
    typer.echo(f"  total: {sum(c.values())}")

    from .blocklist import blocklist_counts as _counts

    b = _counts()
    typer.echo("Blocklist:")
    typer.echo(f"  custom: {b['custom']:,} domains")
    typer.echo(f"  upstream: {b['upstream']:,} domains ({b['sources']} sources)")
    typer.echo(f"  exemptions: {b['exemptions']:,} domains (server=/domain/#)")
    typer.echo(f"  generated: {b['generated']:,} domains")


@app.command("validate")
def validate(
    mode: Annotated[str, typer.Argument(help="Mode to validate")],
) -> None:
    """Validate rendered config files without applying (audit H8).

    Runs nft -c -f + dnsmasq --test on a rendered copy + allowlist check.
    """
    errors = firewall.validate(mode)
    if errors:
        for err in errors:
            typer.echo(f"FAIL: {err}", err=True)
        raise typer.Exit(code=1)
    typer.echo("Config valid")


@app.command("deploy-policies")
def deploy_policies_cmd() -> None:
    """Deploy browser policies (60-ark.sh section 13 target — audit H8)."""
    deploy_policies()


def main() -> None:
    """CLI entry point — runs the typer app."""
    app()
