#!/usr/bin/python3
"""Blocklist manager — download, maintain, and generate dnsmasq hosts files.

Usage:
  blocklist download [-f] [-n NAME] <url> [<url> ...]  Download upstream blocklists
  blocklist purge [-a] [-y] [UUID|NUM ...]             Remove downloaded blocklist(s)
  blocklist add <domain> [<domain> ...]                 Add domain(s) to custom blocklist
  blocklist toggle [-a] [-p] [-y] <domain>             Toggle domain active/commented
  blocklist search <pattern>                            Regex search across all blocklists
  blocklist generate                                    Merge all lists into blocklist.hosts
  blocklist stats                                       Show domain counts per file
  blocklist verify                                      Check file integrity and status
"""

from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid as _uuid
import zipfile
from collections.abc import Callable
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ── Constants ─────────────────────────────────────────────────────────────────

DOMAINS_DIR: str = "/opt/ark/domains"
REGISTRY_FILE: str = os.path.join(DOMAINS_DIR, ".blocklist-registry.json")
CUSTOM_FILE: str = os.path.join(DOMAINS_DIR, "blocklist-custom.txt")
HOSTS_FILE: str = os.path.join(DOMAINS_DIR, "blocklist.hosts")
EXCLUDE_FILE: str = os.path.join(DOMAINS_DIR, "blocklist-exclude.txt")

DOMAIN_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)
MAX_DOMAIN_LEN: int = 253


# ── Data model ────────────────────────────────────────────────────────────────


class RegistryEntry(TypedDict):
    id: str
    url: str
    file: str
    checksum: str


def _valid_domain(d: str) -> bool:
    """Check if a string is a valid domain name."""
    return bool(d) and len(d) <= MAX_DOMAIN_LEN and DOMAIN_RE.match(d) is not None


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


def _normalize_line(line: str) -> str:
    """Strip comment prefix and whitespace for domain comparison."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return stripped[1:].strip()
    return stripped


def _read_exclude() -> set[str]:
    """Read the exclude file. Returns empty set if missing."""
    if not os.path.isfile(EXCLUDE_FILE):
        return set()
    result: set[str] = set()
    with open(EXCLUDE_FILE) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                result.add(stripped.lower().strip())
    return result


def _write_exclude(domains: set[str]) -> None:
    """Write the exclude file. Removes the file if empty."""
    if not domains:
        if os.path.isfile(EXCLUDE_FILE):
            remove_immutable(EXCLUDE_FILE)
            os.remove(EXCLUDE_FILE)
        return
    ensure_domains_dir()
    remove_immutable(EXCLUDE_FILE)
    with open(EXCLUDE_FILE, "w") as f:
        f.write("# Auto-maintained — domains excluded from generation\n")
        for d in sorted(domains):
            f.write(f"{d}\n")
    os.chown(EXCLUDE_FILE, 0, 0)
    os.chmod(EXCLUDE_FILE, 0o644)


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
    result = subprocess.run(
        ["chattr", "-i", path],
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def set_immutable(path: str) -> None:
    """Set immutable flag on a file."""
    subprocess.run(
        ["chattr", "+i", path],
        stderr=subprocess.DEVNULL,
    )


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


def extract_zip(raw_bytes: bytes) -> str:
    """Extract hosts/domain file content from a ZIP archive."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        for name in zf.namelist():
            lower = name.lower()
            if lower.endswith((".txt", ".hosts", ".conf")) or "host" in lower or "domain" in lower:
                return zf.read(name).decode("utf-8", errors="replace")
        return zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")


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

        content: str
        if raw_content[:2] == b"PK":
            print("  ZIP detected, extracting...")
            try:
                content = extract_zip(raw_content)
            except zipfile.BadZipFile:
                print(f"Failed to extract {url}: corrupt ZIP archive")
                continue
        elif raw_content[:2] == b"\x1f\x8b":
            print("  Gzip detected, decompressing...")
            try:
                content = gzip.decompress(raw_content).decode("utf-8", errors="replace")
            except Exception as e:
                print(f"Failed to decompress {url}: {e}")
                continue
        else:
            content = raw_content.decode("utf-8", errors="replace")

        sample: list[str] = content.splitlines()[:100]
        if any(line.strip().startswith("||") for line in sample):
            print(f"ABP format detected in {url} — use a hosts/plain-domain format list instead")
            continue

        domain_list: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if not parts:
                continue
            candidate = parts[-1].lower().strip()
            if _valid_domain(candidate):
                domain_list.append(candidate)
        if not domain_list:
            print(f"No domains found in {url} — not a valid blocklist")
            continue

        try:
            with open(filepath, "w") as f:
                f.write("\n".join(domain_list) + "\n")
        except OSError as e:
            print(f"Failed to save {filepath}: {e}")
            continue

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


def build_purge_list() -> list[tuple[int, RegistryEntry, int]]:
    """Build numbered list of upstream blocklists for purge selection.

    Returns list of (position, entry, domain_count) tuples.
    """
    registry: list[RegistryEntry] = sync_registry()
    purge_list: list[tuple[int, RegistryEntry, int]] = []

    position: int = 0
    for entry in registry:
        filename: str = entry["file"]
        if not filename.startswith("blocklist-") or filename == "blocklist-custom.txt":
            continue
        filepath: str = os.path.join(DOMAINS_DIR, filename)
        if not os.path.isfile(filepath):
            continue
        position += 1
        count: int = 0
        with open(filepath) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    count += 1
        purge_list.append((position, entry, count))

    return purge_list


def prompt_selection(purge_list: list[tuple[int, RegistryEntry, int]]) -> list[RegistryEntry]:
    """Display numbered list and prompt user to select entries to purge."""
    print("Available upstream blocklists:\n")
    print(f"  {'#':>3}  {'FILE':<40} {'URL':<50} {'DOMAINS':>10}")
    for pos, entry, count in purge_list:
        print(f"  {pos:>3}  {entry['file']:<40} {entry['url']:<50} {count:>10,}")

    raw: str = input("\nEnter number(s) to purge (e.g. 1 3): ").strip()
    if not raw:
        sys.exit("Purge cancelled.")

    selected: list[RegistryEntry] = []
    for token in raw.split():
        if not token.isdigit():
            sys.exit(f"Error: invalid position: {token}")
        num = int(token)
        matches = [e for p, e, _ in purge_list if p == num]
        if not matches:
            sys.exit(f"Error: invalid position: {num}")
        selected.append(matches[0])

    return selected


def confirm_purge(
    entries: list[RegistryEntry],
    purge_hosts: bool,
    purge_registry: bool,
) -> bool:
    """Show confirmation prompt for purge operation. Returns True if confirmed."""
    print("\nAbout to purge:")
    for entry in entries:
        filepath: str = os.path.join(DOMAINS_DIR, entry["file"])
        count: int = 0
        if os.path.isfile(filepath):
            with open(filepath) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        count += 1
        print(f"  {entry['file']} ({entry['url']}) — {count:,} domains")

    if purge_hosts and os.path.isfile(HOSTS_FILE):
        print(f"  {HOSTS_FILE} will be deleted.")
    if purge_registry:
        print("  Registry will be reset.")

    answer: str = input("\nProceed? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def cmd_purge(args: argparse.Namespace) -> None:
    """Remove downloaded blocklist(s) with interactive selection and confirmation."""
    purge_all: bool = args.all
    targets: list[str] = args.targets
    skip_confirm: bool = args.yes

    if purge_all and targets:
        sys.exit("Error: cannot use --all with specific targets")

    purge_list: list[tuple[int, RegistryEntry, int]] = build_purge_list()

    if not purge_list:
        print("No upstream blocklists to purge.")
        return

    selected: list[RegistryEntry] = []

    if purge_all:
        selected = [entry for _, entry, _ in purge_list]
    elif targets:
        for target in targets:
            if target.isdigit():
                num = int(target)
                matches = [e for p, e, _ in purge_list if p == num]
                if not matches:
                    sys.exit(f"Error: invalid position: {num}")
                selected.append(matches[0])
            else:
                matches = [e for _, e, _ in purge_list if e["id"].startswith(target)]
                if not matches:
                    sys.exit(f"Error: no blocklist with id '{target}'")
                if len(matches) > 1:
                    sys.exit(
                        f"Error: ambiguous UUID prefix '{target}' — matches {len(matches)} entries"
                    )
                selected.append(matches[0])
    else:
        selected = prompt_selection(purge_list)

    seen_ids: set[str] = set()
    unique: list[RegistryEntry] = []
    for entry in selected:
        if entry["id"] not in seen_ids:
            seen_ids.add(entry["id"])
            unique.append(entry)
    selected = unique

    if not skip_confirm:
        if not confirm_purge(selected, purge_all, purge_all):
            print("Purge cancelled.")
            return

    for entry in selected:
        filepath: str = os.path.join(DOMAINS_DIR, entry["file"])
        if os.path.isfile(filepath):
            remove_immutable(filepath)
            os.remove(filepath)
            print(f"Deleted: {entry['file']}")

    if purge_all:
        update_registry([])
        print("Registry reset.")
    else:
        registry: list[RegistryEntry] = load_registry()
        deleted_ids: set[str] = {e["id"] for e in selected}
        registry = [e for e in registry if e["id"] not in deleted_ids]
        update_registry(registry)
        print(f"Removed {len(selected)} registry entry/entries.")

    if purge_all and os.path.isfile(HOSTS_FILE):
        remove_immutable(HOSTS_FILE)
        os.remove(HOSTS_FILE)
        print(f"Deleted: {HOSTS_FILE}")

    print("Regenerating blocklist.hosts...")
    _run_generate()


def cmd_add(args: argparse.Namespace) -> None:
    """Add domain(s) to the custom blocklist."""
    domains: list[str] = [d.lower().strip() for d in args.domains]

    for domain in domains:
        if not _valid_domain(domain):
            sys.exit(f"Invalid domain format: {domain}")

    ensure_domains_dir()

    lines: list[str] = []
    active: set[str] = set()
    commented: dict[str, int] = {}

    if os.path.isfile(CUSTOM_FILE):
        with open(CUSTOM_FILE) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            norm = _normalize_line(line)
            if norm and not line.strip().startswith("#"):
                active.add(norm)
            elif norm:
                commented[norm] = i

    changed: bool = False
    exclude_changed: bool = False
    current_exclude: set[str] = _read_exclude()

    for domain in domains:
        if domain in active:
            print(f"Already in blocklist: {domain}")
            continue

        if domain in commented:
            idx = commented[domain]
            lines[idx] = f"{domain}\n"
            active.add(domain)
            changed = True
            if domain in current_exclude:
                current_exclude.discard(domain)
                exclude_changed = True
            print(f"Uncommented: {domain}")
            continue

        if not lines:
            lines = ["# Custom blocklist — user-maintained additions\n"]

        lines.append(f"{domain}\n")
        active.add(domain)
        changed = True
        if domain in current_exclude:
            current_exclude.discard(domain)
            exclude_changed = True
        print(f"Added: {domain}")

    if changed:
        with open(CUSTOM_FILE, "w") as f:
            f.writelines(lines)

    if exclude_changed:
        _write_exclude(current_exclude)

    if changed:
        _run_generate()


def cmd_toggle(args: argparse.Namespace) -> None:
    """Toggle domain active/commented in the custom blocklist."""
    domain: str = args.domain.lower().strip()
    all_matches: bool = args.all
    purge: bool = args.purge
    skip_confirm: bool = args.yes

    if not os.path.isfile(CUSTOM_FILE):
        sys.exit(f"Error: {CUSTOM_FILE} not found")

    with open(CUSTOM_FILE) as f:
        lines: list[str] = f.readlines()

    def _match(line: str) -> bool:
        norm = _normalize_line(line)
        if norm == domain:
            return True
        if all_matches and norm.endswith("." + domain):
            return True
        return False

    matches: list[tuple[int, str, bool]] = []
    for i, line in enumerate(lines):
        norm = _normalize_line(line)
        if _match(line):
            is_active = not line.strip().startswith("#")
            matches.append((i, norm, is_active))

    if not matches:
        msg = f"No entries found for: {domain}" if all_matches else f"Domain not found: {domain}"
        print(msg)
        return

    if purge:
        if not skip_confirm:
            scope = f" and {len(matches) - 1} subdomains" if len(matches) > 1 else ""
            confirm = input(
                f"Permanently delete '{domain}'{scope}? Type domain to confirm: "
            ).strip().lower()
            if confirm != domain:
                print("Aborted.")
                return
        new_lines = [line for i, line in enumerate(lines) if not _match(line)]
        affected = len(matches)
        with open(CUSTOM_FILE, "w") as f:
            f.writelines(new_lines)
        exclude_domains = [norm for _, norm, _ in matches]
        current_exclude = _read_exclude()
        current_exclude.update(exclude_domains)
        _write_exclude(current_exclude)
        scope = f" ({affected} entries)" if affected > 1 else ""
        print(f"Deleted: {domain}{scope}")
        _run_generate()
        return

    active_count = sum(1 for _, _, a in matches if a)
    commented_count = len(matches) - active_count

    if active_count and commented_count:
        print(f"\nFound {len(matches)} entries matching '{domain}':\n")
        for i, (idx, norm, is_active) in enumerate(matches, 1):
            state = "active" if is_active else "commented"
            print(f"  {i}. {norm:<40} ({state})")

        if not skip_confirm:
            while True:
                choice = input("\n[C]omment all / [U)ncomment all / [Q]uit: ").strip().lower()
                if choice in ("c", "u", "q"):
                    break
                print("Invalid choice. Enter C, U, or Q.")
            if choice == "q":
                print("Toggle cancelled.")
                return
            comment_action = choice == "c"
        else:
            comment_action = True
    else:
        is_active = matches[0][2]
        comment_action = is_active

        if not skip_confirm:
            if comment_action:
                answer = input(f"\nComment out '{domain}'? [y/N]: ").strip().lower()
            else:
                answer = input(f"\nUncomment '{domain}'? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print("Toggle cancelled.")
                return

    new_lines: list[str] = []
    affected: int = 0
    exclude_domains: list[str] = []

    for i, line in enumerate(lines):
        if _match(line):
            norm = _normalize_line(line)
            if comment_action:
                new_lines.append(f"# {norm}\n")
                exclude_domains.append(norm)
            else:
                new_lines.append(f"{norm}\n")
            affected += 1
        else:
            new_lines.append(line)

    with open(CUSTOM_FILE, "w") as f:
        f.writelines(new_lines)

    current_exclude = _read_exclude()
    if comment_action:
        current_exclude.update(exclude_domains)
    else:
        for d in exclude_domains:
            current_exclude.discard(d)
    _write_exclude(current_exclude)

    action = "Commented out" if comment_action else "Uncommented"
    scope = f" ({affected} entries)" if affected > 1 else ""
    print(f"{action}: {domain}{scope}")
    _run_generate()


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


def _run_generate() -> None:
    """Core generate logic shared by cmd_generate and cmd_purge."""
    import polars as pl
    from contextlib import contextmanager

    if TYPE_CHECKING:
        from tqdm import tqdm
    else:
        try:
            from tqdm import tqdm
        except ImportError:

            @contextmanager
            def tqdm(*args: object, **kwargs: object):  # type: ignore[misc]
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
        print("No blocklist files found. Nothing to generate.")
        return

    for filepath in upstream_files:
        if os.path.getsize(filepath) == 0:
            sys.exit(f"Error: {filepath} is empty. Re-run 'blocklist download'.")

    if has_custom and os.path.getsize(CUSTOM_FILE) == 0:
        sys.exit(f"Error: {CUSTOM_FILE} is empty. Add domains or remove it.")

    def _read_domains(filepath: str) -> list[str]:
        domains: list[str] = []
        with open(filepath) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and _valid_domain(stripped):
                    domains.append(stripped)
        return domains

    all_domains: list[str] = []
    for filepath in upstream_files:
        all_domains.extend(_read_domains(filepath))
    if has_custom:
        all_domains.extend(_read_domains(CUSTOM_FILE))

    exclude: set[str] = _read_exclude()

    df: pl.DataFrame = (
        pl.DataFrame({"domain": all_domains})
        .unique(subset=["domain"])
        .sort("domain")
    )

    if exclude:
        before: int = len(df)
        df = df.filter(~pl.col("domain").is_in(list(exclude)))
        excluded: int = before - len(df)
        if excluded:
            print(f"  Excluded: {excluded} domain(s) from blocklist-exclude.txt")

    series: pl.Series = df["domain"]
    total: int = len(series)

    tmp_fd: int
    tmp_path: str
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=DOMAINS_DIR, prefix=".blocklist.hosts.", suffix=".tmp"
    )
    os.close(tmp_fd)

    try:
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
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        sys.exit(f"Error: generate failed — {e}")


def cmd_generate(args: argparse.Namespace) -> None:
    """Merge all blocklist files into blocklist.hosts."""
    _run_generate()


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

    if os.path.isfile(EXCLUDE_FILE):
        exclude_count: int = len(_read_exclude())
        print(f"  Exclude list: {exclude_count} domain(s)")
    else:
        print("  Exclude list: not present")


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="blocklist",
        description="Blocklist manager — download, maintain, and generate dnsmasq hosts files.",
        epilog="Run 'blocklist <subcommand> --help' for subcommand flags and details.",
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

    pg = sub.add_parser("purge", help="Remove downloaded blocklist(s)")
    pg.add_argument("targets", nargs="*", help="UUID(s) or position number(s) to purge")
    pg.add_argument("-a", "--all", action="store_true", help="Purge all upstream blocklists")
    pg.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    ad = sub.add_parser("add", help="Add domain(s) to custom blocklist")
    ad.add_argument("domains", nargs="+", help="Domain(s) to add (bare, lowercase)")

    tg = sub.add_parser("toggle", help="Toggle domain active/commented in custom blocklist")
    tg.add_argument("domain", help="Domain to toggle")
    tg.add_argument(
        "-a", "--all", action="store_true",
        help="Toggle all subdomains matching the domain"
    )
    tg.add_argument(
        "-p", "--purge", action="store_true",
        help="Permanently delete instead of toggling"
    )
    tg.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation prompt"
    )

    se = sub.add_parser("search", help="Regex search across all blocklists")
    se.add_argument("pattern", help="Regex pattern to search for")

    sub.add_parser("generate", help="Merge all lists into blocklist.hosts")
    sub.add_parser("stats", help="Show domain counts per file")
    sub.add_parser("verify", help="Check file integrity and status")

    return parser


def main() -> None:
    """Entry point."""
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()

    check_root()
    check_chattr()
    check_polars()

    commands: dict[str, Callable[[argparse.Namespace], None]] = {
        "download": cmd_download,
        "purge": cmd_purge,
        "add": cmd_add,
        "toggle": cmd_toggle,
        "search": cmd_search,
        "generate": cmd_generate,
        "stats": cmd_stats,
        "verify": cmd_verify,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
