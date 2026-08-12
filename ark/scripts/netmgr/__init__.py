"""netmgr — network management for the lockdown system.

Full typer CLI. ``main()`` lives here so both ``netmgr.py`` (standalone
shim) and ``__main__.py`` (``python -m netmgr``) can import it. Blocklist
commands are flattened to top-level — ``netmgr generate`` not
``netmgr blocklist generate``.
"""
from __future__ import annotations

import json
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
app.add_typer(ns_app, name="namespace")
app.add_typer(sys_app, name="system")
app.add_typer(al_app, name="allowlist")
app.add_typer(eg_app, name="exec-grant")


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
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
    name: Annotated[str | None, typer.Option("--name", "-n")] = None,
) -> None:
    """Download upstream blocklist(s)."""
    from .blocklist import download as _download

    _download(urls=urls, force=force, name=name)


@app.command()
def add(
    domains: Annotated[list[str], typer.Argument(help="Domain(s) to add")],
) -> None:
    """Add domain(s) to custom blocklist."""
    from .blocklist import add as _add

    _add(domains=domains)


@app.command()
def toggle(
    domain: Annotated[str, typer.Argument(help="Domain to toggle")],
    all_domains: Annotated[bool, typer.Option("--all")] = False,
    purge: Annotated[bool, typer.Option("--purge")] = False,
    yes: Annotated[bool, typer.Option("-y", "--yes")] = False,
) -> None:
    """Toggle domain active/commented."""
    from .blocklist import toggle as _toggle

    _toggle(domain=domain, all_domains=all_domains, purge=purge, yes=yes)


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
    opslog.configure(
        "netmgr",
        file="{{ .Env.ARK_DATA_PATH }}/logs/netmgr.log",
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
    arg: Annotated[
        str | None, typer.Argument(help="Optional single first-arg restriction")
    ] = None,
) -> None:
    """Grant a binary in the namespace allowlist (unrestricted only)."""
    namespace.add_grant(binary, arg)


@eg_app.command("remove")
def eg_remove(
    binary: Annotated[str, typer.Argument(help="Binary name or path to revoke")],
    arg: Annotated[
        str | None, typer.Argument(help="Optional single first-arg restriction")
    ] = None,
) -> None:
    """Revoke a binary grant from the namespace allowlist (unrestricted only)."""
    namespace.remove_grant(binary, arg)


@eg_app.command("deploy")
def eg_deploy(
    bashrc: Annotated[
        str | None,
        typer.Option("--bashrc", help="bashrc to write generated alias block into"),
    ] = None,
) -> None:
    """Rebuild thin shims + inet; optionally sync aliases into a bashrc (root only)."""
    wrappers.deploy(bashrc_path=bashrc)


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
