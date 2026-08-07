"""Blocklist library — download, maintain, and generate the dnsmasq blocklist.

Migrated from ark/scripts/blocklist.py (P6). Exposes library functions
consumed by the netmgr CLI (P8) and ark.py (P9): a pure library with no
CLI entry point and no sys.exit. Runtime files stay at the domains root
($ARK_DATA_PATH/domains): the registry (.blocklist-registry.json), the
exclude list, downloaded blocklist-*.txt sources, and the generated
blocklist.<format>.conf output. The custom list and the wildcard exceptions
file (blocklist-exceptions.txt) live in focused/.
"""
from __future__ import annotations

import glob
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid as _uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import opslog

if TYPE_CHECKING:
    from tqdm import tqdm

# ── Constants ─────────────────────────────────────────────────────────────────

# Gomplate-templated constants (rendered from config.env by 60-ark.sh).
# P6: output is the dnsmasq format file (blocklist.dnsmasq.conf); the custom
# list lives in focused/; the registry/exclude/downloads stay at the root.
DOMAINS_DIR = "{{ .Env.ARK_DATA_PATH }}/domains"
REGISTRY_FILE = f"{DOMAINS_DIR}/.blocklist-registry.json"
CUSTOM_FILE = f"{DOMAINS_DIR}/focused/blocklist-custom.txt"
EXCEPTIONS_FILE = f"{DOMAINS_DIR}/focused/blocklist-exceptions.txt"
EXCLUDE_FILE = f"{DOMAINS_DIR}/blocklist-exclude.txt"
OUTPUT_FORMAT = "{{ .Env.BLOCKLIST_OUTPUT_FORMAT }}"
OUTPUT_FILE = f"{DOMAINS_DIR}/blocklist.{OUTPUT_FORMAT}.conf"
BATCH_SIZE = int("{{ .Env.BLOCKLIST_BATCH_SIZE }}")
DOWNLOAD_TIMEOUT = int("{{ .Env.BLOCKLIST_DOWNLOAD_TIMEOUT }}")
USER_AGENT = "{{ .Env.BLOCKLIST_USER_AGENT }}"

DOMAIN_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)
MAX_DOMAIN_LEN: int = 253


class BlocklistError(Exception):
    """Raised when a blocklist operation fails."""


# ── Data model ────────────────────────────────────────────────────────────────


class RegistryEntry(TypedDict):
    id: str
    url: str
    file: str
    checksum: str


def _valid_domain(d: str) -> bool:
    """Check if a string is a valid domain name."""
    return bool(d) and len(d) <= MAX_DOMAIN_LEN and DOMAIN_RE.match(d) is not None


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


def update_registry(entries: list[RegistryEntry]) -> None:
    """Write registry with corruption protection. No immutable flag.

    The registry was previously +i'd; a stale flag from an old deployment is
    cleared here (best-effort) and never re-applied — the write-hot file must
    not toggle the flag on every registry write.
    """
    remove_immutable(REGISTRY_FILE)

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
    except Exception as e:
        if backup and os.path.isfile(backup):
            shutil.move(backup, REGISTRY_FILE)
        raise BlocklistError(f"Error updating registry: {e}") from e
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
    """Create registry if it doesn't exist. No immutable flag (write-hot file)."""
    if os.path.isfile(REGISTRY_FILE):
        return
    ensure_domains_dir()
    update_registry([])


# ── Checksum ──────────────────────────────────────────────────────────────────


def sha256_file(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Library functions (CLI layer in __init__.py maps typer → these) ──────────


def extract_zip(raw_bytes: bytes) -> str:
    """Extract hosts/domain file content from a ZIP archive."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        for name in zf.namelist():
            lower = name.lower()
            if lower.endswith((".txt", ".hosts", ".conf")) or "host" in lower or "domain" in lower:
                return zf.read(name).decode("utf-8", errors="replace")
        return zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")


def download(urls: list[str], force: bool = False, name: str | None = None) -> None:
    """Download one or more upstream blocklist URLs into the registry."""
    if name and len(urls) > 1:
        raise BlocklistError("--name can only be used with a single URL")

    init_registry()
    ensure_domains_dir()
    registry: list[RegistryEntry] = load_registry()

    for url in urls:
        norm: str = normalize_url(url)

        if not force and any(normalize_url(e["url"]) == norm for e in registry):
            opslog.info("Already downloaded: %s", url)
            continue

        entry_id: str = name if name else _uuid.uuid4().hex[:6]
        filename: str = f"blocklist-{entry_id}.txt"
        filepath: str = os.path.join(DOMAINS_DIR, filename)

        opslog.info("Downloading: %s", url)
        try:
            req: Request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                if resp.status != 200:
                    opslog.error("Failed to download %s: HTTP %s", url, resp.status)
                    continue
                raw_content: bytes = resp.read()
        except Exception as e:
            opslog.error("Failed to download %s: %s", url, e)
            continue

        content: str
        if raw_content[:2] == b"PK":
            opslog.info("  ZIP detected, extracting...")
            try:
                content = extract_zip(raw_content)
            except zipfile.BadZipFile:
                opslog.error("Failed to extract %s: corrupt ZIP archive", url)
                continue
        elif raw_content[:2] == b"\x1f\x8b":
            opslog.info("  Gzip detected, decompressing...")
            try:
                content = gzip.decompress(raw_content).decode("utf-8", errors="replace")
            except Exception as e:
                opslog.error("Failed to decompress %s: %s", url, e)
                continue
        else:
            content = raw_content.decode("utf-8", errors="replace")

        sample: list[str] = content.splitlines()[:100]
        if any(line.strip().startswith("||") for line in sample):
            opslog.error("ABP format detected in %s — use a hosts/plain-domain format list instead", url)
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
            opslog.error("No domains found in %s — not a valid blocklist", url)
            continue

        try:
            with open(filepath, "w") as f:
                f.write("\n".join(domain_list) + "\n")
        except OSError as e:
            opslog.error("Failed to save %s: %s", filepath, e)
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
        opslog.info("  Saved: %s (%d domains, sha256:%s…)", filename, len(domain_list), checksum[:16])
        opslog.info("  Preview: %s%s", ", ".join(preview), "…" if len(domain_list) > 10 else "")


def _build_purge_list() -> list[tuple[int, RegistryEntry, int]]:
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


def _prompt_selection(purge_list: list[tuple[int, RegistryEntry, int]]) -> list[RegistryEntry]:
    """Display numbered list and prompt user to select entries to purge."""
    opslog.info("Available upstream blocklists:\n")
    opslog.info("  %3s  %-40s %-50s %10s", "#", "FILE", "URL", "DOMAINS")
    for pos, entry, count in purge_list:
        opslog.info("  %3d  %-40s %-50s %10s", pos, entry["file"], entry["url"], f"{count:,}")

    raw: str = input("\nEnter number(s) to purge (e.g. 1 3): ").strip()
    if not raw:
        raise BlocklistError("Purge cancelled.")

    selected: list[RegistryEntry] = []
    for token in raw.split():
        if not token.isdigit():
            raise BlocklistError(f"invalid position: {token}")
        num = int(token)
        matches = [e for p, e, _ in purge_list if p == num]
        if not matches:
            raise BlocklistError(f"invalid position: {num}")
        selected.append(matches[0])

    return selected


def _confirm_purge(
    entries: list[RegistryEntry],
    purge_hosts: bool,
    purge_registry: bool,
) -> bool:
    """Show confirmation prompt for purge operation. Returns True if confirmed."""
    opslog.info("\nAbout to purge:")
    for entry in entries:
        filepath: str = os.path.join(DOMAINS_DIR, entry["file"])
        count: int = 0
        if os.path.isfile(filepath):
            with open(filepath) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        count += 1
        opslog.info("  %s (%s) — %s domains", entry["file"], entry["url"], f"{count:,}")

    if purge_hosts and os.path.isfile(OUTPUT_FILE):
        opslog.info("  %s will be deleted.", OUTPUT_FILE)
    if purge_registry:
        opslog.info("  Registry will be reset.")

    answer: str = input("\nProceed? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def purge(*, all_domains: bool = False, targets: list[str] | None = None, yes: bool = False) -> None:
    """Remove downloaded blocklist(s) with interactive selection and confirmation."""
    if all_domains and targets:
        raise BlocklistError("cannot use --all with specific targets")

    purge_list: list[tuple[int, RegistryEntry, int]] = _build_purge_list()

    if not purge_list:
        opslog.info("No upstream blocklists to purge.")
        return

    selected: list[RegistryEntry] = []

    if all_domains:
        selected = [entry for _, entry, _ in purge_list]
    elif targets:
        for target in targets:
            if target.isdigit():
                num = int(target)
                matches = [e for p, e, _ in purge_list if p == num]
                if not matches:
                    raise BlocklistError(f"invalid position: {num}")
                selected.append(matches[0])
            else:
                matches = [e for _, e, _ in purge_list if e["id"].startswith(target)]
                if not matches:
                    raise BlocklistError(f"no blocklist with id '{target}'")
                if len(matches) > 1:
                    raise BlocklistError(
                        f"ambiguous UUID prefix '{target}' — matches {len(matches)} entries"
                    )
                selected.append(matches[0])
    else:
        selected = _prompt_selection(purge_list)

    seen_ids: set[str] = set()
    unique: list[RegistryEntry] = []
    for entry in selected:
        if entry["id"] not in seen_ids:
            seen_ids.add(entry["id"])
            unique.append(entry)
    selected = unique

    if not yes:
        if not _confirm_purge(selected, all_domains, all_domains):
            opslog.info("Purge cancelled.")
            return

    for entry in selected:
        filepath: str = os.path.join(DOMAINS_DIR, entry["file"])
        if os.path.isfile(filepath):
            remove_immutable(filepath)
            os.remove(filepath)
            opslog.info("Deleted: %s", entry["file"])

    if all_domains:
        update_registry([])
        opslog.info("Registry reset.")
    else:
        registry: list[RegistryEntry] = load_registry()
        deleted_ids: set[str] = {e["id"] for e in selected}
        registry = [e for e in registry if e["id"] not in deleted_ids]
        update_registry(registry)
        opslog.info("Removed %d registry entry/entries.", len(selected))

    if all_domains and os.path.isfile(OUTPUT_FILE):
        remove_immutable(OUTPUT_FILE)
        os.remove(OUTPUT_FILE)
        opslog.info("Deleted: %s", OUTPUT_FILE)

    opslog.info("Regenerating %s...", os.path.basename(OUTPUT_FILE))
    generate()


def add(domains: list[str]) -> None:
    """Add domain(s) to the custom blocklist."""
    clean: list[str] = [d.lower().strip() for d in domains]

    for domain in clean:
        if not _valid_domain(domain):
            raise BlocklistError(f"Invalid domain format: {domain}")

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

    for domain in clean:
        if domain in active:
            opslog.info("Already in blocklist: %s", domain)
            continue

        if domain in commented:
            idx = commented[domain]
            lines[idx] = f"{domain}\n"
            active.add(domain)
            changed = True
            if domain in current_exclude:
                current_exclude.discard(domain)
                exclude_changed = True
            opslog.info("Uncommented: %s", domain)
            continue

        if not lines:
            lines = ["# Custom blocklist — user-maintained additions\n"]

        lines.append(f"{domain}\n")
        active.add(domain)
        changed = True
        if domain in current_exclude:
            current_exclude.discard(domain)
            exclude_changed = True
        opslog.info("Added: %s", domain)

    if changed:
        with open(CUSTOM_FILE, "w") as f:
            f.writelines(lines)

    if exclude_changed:
        _write_exclude(current_exclude)

    if changed:
        generate()


def toggle(domain: str, *, all_domains: bool = False, purge: bool = False, yes: bool = False) -> None:
    """Toggle domain active/commented in the custom blocklist."""
    target: str = domain.lower().strip()

    if not os.path.isfile(CUSTOM_FILE):
        raise BlocklistError(f"{CUSTOM_FILE} not found")

    with open(CUSTOM_FILE) as f:
        lines: list[str] = f.readlines()

    def _match(line: str) -> bool:
        norm = _normalize_line(line)
        if norm == target:
            return True
        if all_domains and norm.endswith("." + target):
            return True
        return False

    matches: list[tuple[int, str, bool]] = []
    for i, line in enumerate(lines):
        norm = _normalize_line(line)
        if _match(line):
            is_active = not line.strip().startswith("#")
            matches.append((i, norm, is_active))

    if not matches:
        msg = f"No entries found for: {target}" if all_domains else f"Domain not found: {target}"
        opslog.info("%s", msg)
        return

    if purge:
        if not yes:
            scope = f" and {len(matches) - 1} subdomains" if len(matches) > 1 else ""
            confirm = input(
                f"Permanently delete '{target}'{scope}? Type domain to confirm: "
            ).strip().lower()
            if confirm != target:
                opslog.info("Aborted.")
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
        opslog.info("Deleted: %s%s", target, scope)
        generate()
        return

    active_count = sum(1 for _, _, a in matches if a)
    commented_count = len(matches) - active_count

    if active_count and commented_count:
        opslog.info("\nFound %d entries matching '%s':\n", len(matches), target)
        for i, (_idx, norm, is_active) in enumerate(matches, 1):
            state = "active" if is_active else "commented"
            opslog.info("  %d. %-40s (%s)", i, norm, state)

        if not yes:
            while True:
                choice = input("\n[C]omment all / [U)ncomment all / [Q]uit: ").strip().lower()
                if choice in ("c", "u", "q"):
                    break
                opslog.info("Invalid choice. Enter C, U, or Q.")
            if choice == "q":
                opslog.info("Toggle cancelled.")
                return
            comment_action = choice == "c"
        else:
            comment_action = True
    else:
        is_active = matches[0][2]
        comment_action = is_active

        if not yes:
            if comment_action:
                answer = input(f"\nComment out '{target}'? [y/N]: ").strip().lower()
            else:
                answer = input(f"\nUncomment '{target}'? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                opslog.info("Toggle cancelled.")
                return

    new_lines: list[str] = []
    affected: int = 0
    exclude_domains: list[str] = []

    for _i, line in enumerate(lines):
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
    opslog.info("%s: %s%s", action, target, scope)
    generate()


def search(pattern: str) -> None:
    """Regex search across all blocklist files."""
    try:
        compiled: re.Pattern[str] = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise BlocklistError(f"Invalid regex: {e}") from e

    files: list[str] = sorted(glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt")))
    if os.path.isfile(CUSTOM_FILE):
        files.append(CUSTOM_FILE)

    if not files:
        opslog.info("No blocklist files found.")
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
                        opslog.info("  %s: %s", filename, stripped)
                        found += 1
        except Exception as e:
            opslog.error("  Error reading %s: %s", filename, e)

    if found == 0:
        opslog.info("No matches for: %s", pattern)
    else:
        opslog.info("\n%d match(es) found", found)


class _NullBar:
    def update(self, n: int = 1) -> None:
        pass


@contextmanager
def _progress(total: int) -> Iterator[_NullBar | tqdm]:
    """Yield a tqdm progress bar, falling back to a no-op bar if unavailable."""
    try:
        from tqdm import tqdm
    except ImportError:
        yield _NullBar()
        return
    with tqdm(
        total=total,
        unit="domains",
        desc="Generating",
        ncols=80,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    ) as pbar:
        yield pbar


def generate() -> None:
    """Merge all blocklist files into blocklist.<format>.conf (local=/domain/)."""
    import polars as pl

    ensure_domains_dir()

    upstream_files: list[str] = sorted(
        glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt"))
    )
    has_custom: bool = os.path.isfile(CUSTOM_FILE)

    if not upstream_files and not has_custom:
        opslog.info("No blocklist files found. Nothing to generate.")
        return

    for filepath in upstream_files:
        if os.path.getsize(filepath) == 0:
            raise BlocklistError(f"{filepath} is empty. Re-run 'download'.")

    if has_custom and os.path.getsize(CUSTOM_FILE) == 0:
        raise BlocklistError(f"{CUSTOM_FILE} is empty. Add domains or remove it.")

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

    exceptions: list[str] = []
    if os.path.isfile(EXCEPTIONS_FILE):
        exceptions = _read_domains(EXCEPTIONS_FILE)

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
            opslog.info("  Excluded: %d domain(s) from blocklist-exclude.txt", excluded)

    series: pl.Series = df["domain"]
    total: int = len(series)

    tmp_fd: int
    tmp_path: str
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=DOMAINS_DIR, prefix=".blocklist.", suffix=".tmp"
    )
    os.close(tmp_fd)

    try:
        with open(tmp_path, "w") as f, _progress(total) as pbar:
            lines: list[str] = []
            for i in range(total):
                domain: str = series[i]
                lines.append(f"local=/{domain}/")
                if len(lines) >= BATCH_SIZE:
                    f.write("\n".join(lines) + "\n")
                    lines.clear()
                    pbar.update(BATCH_SIZE)
            if lines:
                f.write("\n".join(lines) + "\n")
                pbar.update(len(lines))

            if exceptions:
                f.write("\n# Exceptions (unblock from wildcards)\n")
                for exc in exceptions:
                    f.write(f"server=/{exc}/#\n")

        os.chown(tmp_path, 0, 0)
        os.chmod(tmp_path, 0o644)
        os.rename(tmp_path, OUTPUT_FILE)

        opslog.info("Generated: %s", OUTPUT_FILE)
        opslog.info(
            "  %d domains (local=/domain/), %d source file(s)%s",
            total,
            len(upstream_files) + (1 if has_custom else 0),
            f", {len(exceptions)} exception(s) (server=/domain/#)" if exceptions else "",
        )
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise BlocklistError(f"generate failed — {e}") from e


def stats() -> None:
    """Show domain counts per file and total."""
    sync_registry()
    ensure_domains_dir()

    files: list[str] = sorted(
        glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt"))
    )
    if os.path.isfile(CUSTOM_FILE):
        files.append(CUSTOM_FILE)

    if not files:
        opslog.info("No blocklist files found.")
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
        opslog.info("  %s: %d domains", filename, count)

    if os.path.isfile(OUTPUT_FILE):
        hosts_count: int = 0
        with open(OUTPUT_FILE) as f:
            for line in f:
                if line.startswith("local=/"):
                    hosts_count += 1
        opslog.info(
            "  %s: %d domains (generated)",
            os.path.basename(OUTPUT_FILE),
            hosts_count,
        )
    opslog.info("\n  Total: %d domains across %d file(s)", total, len(files))


def verify() -> None:
    """Verify blocklist file integrity and status."""
    sync_registry()
    ensure_domains_dir()

    opslog.info("File verification:")
    files: list[str] = sorted(glob.glob(os.path.join(DOMAINS_DIR, "blocklist-*.txt")))
    if os.path.isfile(CUSTOM_FILE):
        files.append(CUSTOM_FILE)

    if not files:
        opslog.info("  No blocklist files found.")

    for filepath in files:
        filename: str = os.path.basename(filepath)
        exists: bool = os.path.isfile(filepath)
        if exists:
            size: int = os.path.getsize(filepath)
            status: str = "OK" if size > 0 else "EMPTY"
            opslog.info("  %s: %s (%d bytes)", filename, status, size)
        else:
            opslog.info("  %s: MISSING", filename)

    if os.path.isfile(OUTPUT_FILE):
        size = os.path.getsize(OUTPUT_FILE)
        status = "OK" if size > 0 else "EMPTY"
        opslog.info("  %s: %s (%d bytes)", os.path.basename(OUTPUT_FILE), status, size)
    else:
        opslog.info("  %s: MISSING (run 'generate')", os.path.basename(OUTPUT_FILE))

    if os.path.isfile(REGISTRY_FILE):
        entries: list[RegistryEntry] = load_registry()
        opslog.info("  Registry: %d entry/entries", len(entries))
    else:
        opslog.info("  Registry: not initialized")

    if os.path.isfile(EXCLUDE_FILE):
        exclude_count: int = len(_read_exclude())
        opslog.info("  Exclude list: %d domain(s)", exclude_count)
    else:
        opslog.info("  Exclude list: not present")
