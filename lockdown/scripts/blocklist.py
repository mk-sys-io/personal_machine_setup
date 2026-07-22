#!/usr/bin/python3
"""Blocklist manager — download, maintain, and generate dnsmasq hosts files.

Usage:
  blocklist download <url> [<url> ...]   Download upstream blocklists
  blocklist purge <uuid>                 Remove a downloaded blocklist
  blocklist add <domain> [<domain> ...]  Add domain(s) to custom blocklist
  blocklist remove <domain>              Remove domain from custom blocklist
  blocklist search <pattern>             Regex search across all blocklists
  blocklist generate                     Merge all lists into blocklist.hosts
  blocklist stats                        Show domain counts per file
  blocklist verify                       Check file integrity
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid as _uuid
from typing import Any, TypedDict
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ── Constants ─────────────────────────────────────────────────────────────────

DOMAINS_DIR: str = "/opt/lockdown/domains"
REGISTRY_FILE: str = os.path.join(DOMAINS_DIR, ".blocklist-registry.json")
CUSTOM_FILE: str = os.path.join(DOMAINS_DIR, "blocklist-custom.txt")
HOSTS_FILE: str = os.path.join(DOMAINS_DIR, "blocklist.hosts")

DOMAIN_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)


# ── Data model ────────────────────────────────────────────────────────────────


class RegistryEntry(TypedDict):
    id: str
    url: str
    file: str
    checksum: str


# ── Pre-flight checks ─────────────────────────────────────────────────────────


def check_root() -> None:
    """Exit if not running as root."""
    if os.geteuid() != 0:
        sys.exit("Error: sudo required. Run: sudo blocklist ...")


def check_chattr() -> None:
    """Exit if chattr is not installed."""
    if not shutil.which("chattr"):
        sys.exit(
            "chattr not found. Install with: sudo apt install util-linux"
        )


def check_polars() -> None:
    """Exit if polars is not installed."""
    try:
        import polars  # noqa: F401
    except ImportError:
        sys.exit(
            "Error: polars not installed. Run: sudo pip3 install --break-system-packages polars"
        )


def ensure_domains_dir() -> None:
    """Create the domains directory if it doesn't exist."""
    os.makedirs(DOMAINS_DIR, exist_ok=True)


# ── Registry operations ──────────────────────────────────────────────────────


def load_registry() -> list[RegistryEntry]:
    """Load the registry from disk."""
    if not os.path.isfile(REGISTRY_FILE):
        return []
    with open(REGISTRY_FILE) as f:
        return json.load(f)


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication comparison."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    elif netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    return f"{scheme}://{netloc}{path}"


def remove_immutable(path: str) -> bool:
    """Remove immutable flag. Returns True if it was set."""
    try:
        result = os.system(f"chattr -i {path} 2>/dev/null")
        return result == 0
    except Exception:
        return False


def set_immutable(path: str) -> None:
    """Set immutable flag on a file."""
    os.system(f"chattr +i {path} 2>/dev/null")


def update_registry(entries: list[RegistryEntry]) -> None:
    """Write registry with immutable flag handling and corruption protection."""
    had_immutable = remove_immutable(REGISTRY_FILE)

    backup: str | None = None
    if os.path.isfile(REGISTRY_FILE):
        backup = REGISTRY_FILE + ".bak"
        shutil.copy2(REGISTRY_FILE, backup)

    try:
        with open(REGISTRY_FILE, "w") as f:
            json.dump(entries, f, indent=2)
            f.write("\n")
        os.chown(REGISTRY_FILE, 0, 0)
        os.chmod(REGISTRY_FILE, 0o644)

        with open(REGISTRY_FILE) as f:
            json.load(f)

        if had_immutable:
            set_immutable(REGISTRY_FILE)
    except Exception as e:
        if backup and os.path.isfile(backup):
            shutil.move(backup, REGISTRY_FILE)
            if had_immutable:
                set_immutable(REGISTRY_FILE)
        sys.exit(f"Error updating registry: {e}")
    finally:
        if backup and os.path.isfile(backup):
            remove_immutable(backup)
            os.remove(backup)


def sync_registry() -> list[RegistryEntry]:
    """Reconcile registry against actual files on disk."""
    entries = load_registry()
    if not entries:
        return entries

    actual_files: set[str] = {
        os.path.basename(f)
        for f in glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt"))
    }

    synced: list[RegistryEntry] = []
    changed: bool = False
    for entry in entries:
        if entry["file"] in actual_files:
            synced.append(entry)
        else:
            changed = True

    if changed:
        update_registry(synced)

    return synced


def init_registry() -> None:
    """Create registry with immutable flag if it doesn't exist."""
    if os.path.isfile(REGISTRY_FILE):
        return
    ensure_domains_dir()
    update_registry([])
    set_immutable(REGISTRY_FILE)


# ── Checksum ──────────────────────────────────────────────────────────────────


def sha256_file(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Subcommands ───────────────────────────────────────────────────────────────


def cmd_download(args: argparse.Namespace) -> None:
    """Download one or more blocklist URLs."""
    urls: list[str] = args.urls
    force: bool = args.force
    name: str | None = args.name

    if name and len(urls) > 1:
        sys.exit("Error: --name can only be used with a single URL")

    init_registry()
    ensure_domains_dir()
    registry: list[RegistryEntry] = load_registry()

    for url in urls:
        norm: str = normalize_url(url)

        if not force:
            for entry in registry:
                if normalize_url(entry["url"]) == norm:
                    print(f"Already downloaded: {url} (id: {entry['id']})")
                    break
            else:
                pass
            if any(normalize_url(e["url"]) == norm for e in registry):
                continue

        entry_id: str = name if name else _uuid.uuid4().hex[:6]
        filename: str = f"blocklist-{entry_id}.txt"
        filepath: str = os.path.join(DOMAINS_DIR, filename)

        print(f"Downloading: {url}")
        try:
            req: Request = Request(url, headers={"User-Agent": "blocklist-manager/1.0"})
            with urlopen(req, timeout=60) as resp:
                if resp.status != 200:
                    print(f"Failed to download {url}: HTTP {resp.status}")
                    continue
                raw_content: bytes = resp.read()
        except Exception as e:
            print(f"Failed to download {url}: {e}")
            continue

        content: str = raw_content.decode("utf-8", errors="replace")
        domain_list: list[str] = [
            line.strip().split()[-1]
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
            and line.strip().split()
        ]
        if not domain_list:
            print(f"No domains found in {url} — not a valid blocklist")
            continue

        with open(filepath, "w") as f:
            f.write(content)

        checksum: str = sha256_file(filepath)

        registry = [
            e for e in registry
            if not (normalize_url(e["url"]) == norm and not force)
        ]
        if force:
            registry = [e for e in registry if e["id"] != entry_id]

        registry.append(RegistryEntry(
            id=entry_id,
            url=url,
            file=filename,
            checksum=checksum,
        ))

        update_registry(registry)

        preview: list[str] = domain_list[:10]
        print(f"  Saved: {filename} ({len(domain_list)} domains, sha256:{checksum[:16]}…)")
        print(f"  Preview: {', '.join(preview)}{'…' if len(domain_list) > 10 else ''}")


def cmd_purge(args: argparse.Namespace) -> None:
    """Delete a blocklist file and its registry entry."""
    target_id: str = args.uuid
    registry: list[RegistryEntry] = load_registry()

    entry: RegistryEntry | None = None
    for e in registry:
        if e["id"] == target_id:
            entry = e
            break

    if not entry:
        sys.exit(f"Error: no blocklist with id '{target_id}'")

    filepath: str = os.path.join(DOMAINS_DIR, entry["file"])
    if os.path.isfile(filepath):
        remove_immutable(filepath)
        os.remove(filepath)
        print(f"Deleted: {entry['file']}")

    registry = [e for e in registry if e["id"] != target_id]
    update_registry(registry)
    print(f"Removed registry entry: {target_id}")


def cmd_add(args: argparse.Namespace) -> None:
    """Add domain(s) to the custom blocklist."""
    domains: list[str] = [d.lower().strip() for d in args.domains]

    for domain in domains:
        if not DOMAIN_RE.match(domain):
            sys.exit(f"Invalid domain format: {domain}")

    ensure_domains_dir()

    existing: set[str] = set()
    if os.path.isfile(CUSTOM_FILE):
        with open(CUSTOM_FILE) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    existing.add(stripped)

    added: int = 0
    for domain in domains:
        if domain in existing:
            print(f"Already in blocklist: {domain}")
            continue

        if not os.path.isfile(CUSTOM_FILE):
            with open(CUSTOM_FILE, "w") as f:
                f.write("# Custom blocklist — user-maintained additions\n")

        with open(CUSTOM_FILE, "a") as f:
            f.write(f"{domain}\n")

        existing.add(domain)
        added += 1
        print(f"Added: {domain}")

    if added > 0:
        cmd_generate(args)


def cmd_remove(args: argparse.Namespace) -> None:
    """Remove a domain from the custom blocklist."""
    domain: str = args.domain.lower().strip()
    all_matches: bool = args.all
    purge: bool = args.purge

    if not os.path.isfile(CUSTOM_FILE):
        sys.exit(f"Error: {CUSTOM_FILE} not found")

    if purge:
        confirm: str = input(
            f"WARNING: Permanently delete '{domain}' and all matches? "
            f"Type the domain to confirm: "
        ).strip().lower()
        if confirm != domain:
            sys.exit("Aborted.")

    with open(CUSTOM_FILE) as f:
        lines: list[str] = f.readlines()

    new_lines: list[str] = []
    affected: int = 0

    for line in lines:
        stripped: str = line.strip()
        should_match: bool = False

        if stripped == domain:
            should_match = True
        elif all_matches and stripped.endswith("." + domain):
            should_match = True

        if should_match:
            affected += 1
            if not purge:
                new_lines.append(f"# {stripped}\n")
            continue

        new_lines.append(line)

    if affected == 0:
        print(f"Domain not found: {domain}")
        return

    with open(CUSTOM_FILE, "w") as f:
        f.writelines(new_lines)

    action: str = "Purged" if purge else "Commented out"
    scope: str = f" ({affected} entries)" if affected > 1 else ""
    print(f"{action}: {domain}{scope}")

    cmd_generate(args)


def cmd_search(args: argparse.Namespace) -> None:
    """Regex search across all blocklist files."""
    pattern: str = args.pattern

    try:
        compiled: re.Pattern[str] = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        sys.exit(f"Invalid regex: {e}")

    files: list[str] = sorted(glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt")))
    if os.path.isfile(CUSTOM_FILE):
        files.append(CUSTOM_FILE)

    if not files:
        print("No blocklist files found.")
        return

    found: int = 0
    for filepath in files:
        filename: str = os.path.basename(filepath)
        try:
            with open(filepath) as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if compiled.search(stripped):
                        print(f"  {filename}: {stripped}")
                        found += 1
        except Exception as e:
            print(f"  Error reading {filename}: {e}")

    if found == 0:
        print(f"No matches for: {pattern}")
    else:
        print(f"\n{found} match(es) found")


def cmd_generate(args: argparse.Namespace) -> None:
    """Merge all blocklist files into blocklist.hosts."""
    import polars as pl
    from contextlib import contextmanager

    try:
        from tqdm import tqdm
    except ImportError:

        @contextmanager
        def tqdm(*args: object, **kwargs: object) -> object:  # type: ignore[misc]
            class _NullBar:
                def update(self, n: int = 1) -> None:
                    pass

            yield _NullBar()

    ensure_domains_dir()

    upstream_files: list[str] = sorted(
        glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt"))
    )
    has_custom: bool = os.path.isfile(CUSTOM_FILE)

    if not upstream_files and not has_custom:
        sys.exit("No blocklist files found. Run 'blocklist download' first.")

    for filepath in upstream_files:
        if os.path.getsize(filepath) == 0:
            sys.exit(f"Error: {filepath} is empty. Re-run 'blocklist download'.")

    if has_custom and os.path.getsize(CUSTOM_FILE) == 0:
        sys.exit(f"Error: {CUSTOM_FILE} is empty. Add domains or remove it.")

    frames: list[pl.LazyFrame] = []

    for filepath in upstream_files:
        lf = (
            pl.scan_csv(filepath, has_header=False, new_columns=["raw"])
            .filter(~pl.col("raw").str.starts_with("#"))
            .filter(pl.col("raw").str.contains(r"^\S"))
            .select(
                pl.col("raw").str.strip_chars().str.split(" ").list.last().alias("domain")
            )
            .filter(pl.col("domain").str.contains(r"^[a-zA-Z0-9]"))
        )
        frames.append(lf)

    if has_custom:
        custom_lf = (
            pl.scan_csv(CUSTOM_FILE, has_header=False, new_columns=["raw"])
            .filter(~pl.col("raw").str.starts_with("#"))
            .filter(pl.col("raw").str.contains(r"^\S"))
            .select(pl.col("raw").str.strip_chars().alias("domain"))
            .filter(pl.col("domain").str.contains(r"^[a-zA-Z0-9]"))
        )
        frames.append(custom_lf)

    merged: pl.LazyFrame = (
        pl.concat(frames)
        .unique(subset=["domain"])
        .filter(pl.col("domain").is_not_null())
        .sort("domain")
    )

    tmp_fd: int
    tmp_path: str
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=DOMAINS_DIR, prefix=".blocklist.hosts.", suffix=".tmp"
    )
    os.close(tmp_fd)

    try:
        result: pl.DataFrame = merged.collect()
        series: pl.Series = result["domain"]
        total: int = len(series)

        BATCH: int = 100_000

        with open(tmp_path, "w") as f, tqdm(
            total=total,
            unit="domains",
            desc="Generating",
            ncols=80,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        ) as pbar:
            lines: list[str] = []
            for i in range(total):
                domain: str = series[i]
                lines.append(f"0.0.0.0 {domain}")
                lines.append(f":: {domain}")
                if len(lines) >= BATCH * 2:
                    f.write("\n".join(lines) + "\n")
                    lines.clear()
                    pbar.update(BATCH)
            if lines:
                f.write("\n".join(lines) + "\n")
                pbar.update(len(lines) // 2)

        os.chown(tmp_path, 0, 0)
        os.chmod(tmp_path, 0o644)
        os.rename(tmp_path, HOSTS_FILE)

        print(f"Generated: {HOSTS_FILE}")
        print(f"  {total} domains, {total * 2} entries")
        print(f"  Source files: {len(upstream_files) + (1 if has_custom else 0)}")
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def cmd_stats(args: argparse.Namespace) -> None:
    """Show domain counts per file and total."""
    sync_registry()
    ensure_domains_dir()

    files: list[str] = sorted(
        glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt"))
    )
    if os.path.isfile(CUSTOM_FILE):
        files.append(CUSTOM_FILE)

    if not files:
        print("No blocklist files found.")
        return

    total: int = 0
    for filepath in files:
        filename: str = os.path.basename(filepath)
        count: int = 0
        with open(filepath) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    count += 1
        total += count
        print(f"  {filename}: {count} domains")

    if os.path.isfile(HOSTS_FILE):
        hosts_count: int = 0
        with open(HOSTS_FILE) as f:
            for line in f:
                if line.startswith("0.0.0.0 "):
                    hosts_count += 1
        print(f"  blocklist.hosts: {hosts_count} domains (generated)")
    print(f"\n  Total: {total} domains across {len(files)} file(s)")


def cmd_verify(args: argparse.Namespace) -> None:
    """Verify blocklist file integrity and status."""
    sync_registry()
    ensure_domains_dir()

    print("File verification:")
    files: list[str] = sorted(glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt")))
    if os.path.isfile(CUSTOM_FILE):
        files.append(CUSTOM_FILE)

    if not files:
        print("  No blocklist files found.")

    for filepath in files:
        filename: str = os.path.basename(filepath)
        exists: bool = os.path.isfile(filepath)
        if exists:
            size: int = os.path.getsize(filepath)
            status: str = "OK" if size > 0 else "EMPTY"
            print(f"  {filename}: {status} ({size} bytes)")
        else:
            print(f"  {filename}: MISSING")

    if os.path.isfile(HOSTS_FILE):
        size = os.path.getsize(HOSTS_FILE)
        status = "OK" if size > 0 else "EMPTY"
        print(f"  blocklist.hosts: {status} ({size} bytes)")
    else:
        print("  blocklist.hosts: MISSING (run 'blocklist generate')")

    if os.path.isfile(REGISTRY_FILE):
        entries: list[RegistryEntry] = load_registry()
        print(f"  Registry: {len(entries)} entry/entries")
    else:
        print("  Registry: not initialized")


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="blocklist",
        description="Blocklist manager — download, maintain, and generate dnsmasq hosts files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dl = sub.add_parser("download", help="Download upstream blocklist(s)")
    dl.add_argument("urls", nargs="+", help="URL(s) to download")
    dl.add_argument(
        "-f", "--force", action="store_true", help="Force re-download, overwrite existing"
    )
    dl.add_argument(
        "-n", "--name", help="Custom source name (single URL only)"
    )

    pg = sub.add_parser("purge", help="Remove a downloaded blocklist")
    pg.add_argument("uuid", help="Blocklist UUID to purge")

    ad = sub.add_parser("add", help="Add domain(s) to custom blocklist")
    ad.add_argument("domains", nargs="+", help="Domain(s) to add (bare, lowercase)")

    rm = sub.add_parser("remove", help="Remove domain from custom blocklist")
    rm.add_argument("domain", help="Domain to remove")
    rm.add_argument(
        "-a", "--all", action="store_true",
        help="Remove all subdomains matching the domain"
    )
    rm.add_argument(
        "-p", "--purge", action="store_true",
        help="Permanently delete instead of commenting out (requires confirmation)"
    )

    se = sub.add_parser("search", help="Regex search across all blocklists")
    se.add_argument("pattern", help="Regex pattern to search for")

    sub.add_parser("generate", help="Merge all lists into blocklist.hosts")
    sub.add_parser("stats", help="Show domain counts per file")
    sub.add_parser("verify", help="Check file integrity and status")

    return parser


def main() -> None:
    """Entry point."""
    check_root()
    check_chattr()
    check_polars()

    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()

    commands: dict[str, Any] = {
        "download": cmd_download,
        "purge": cmd_purge,
        "add": cmd_add,
        "remove": cmd_remove,
        "search": cmd_search,
        "generate": cmd_generate,
        "stats": cmd_stats,
        "verify": cmd_verify,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
