"""Blocklist management operations — add/toggle/search/purge + stats/verify.

Split from netmgr/blocklist.py (P13). The manager functions share module state
and cross-call ``generate()``, so they stay together; only the zero-coupling
chunks (_config, registry) were extracted.
"""
from __future__ import annotations

import glob
import os
import re

import opslog

from ._config import (
    CUSTOM_FILE,
    DOMAINS_DIR,
    EXCLUDE_FILE,
    OUTPUT_FILE,
    REGISTRY_FILE,
    BlocklistError,
    _normalize_line,
    _valid_domain,
    ensure_domains_dir,
)
from .generate import generate
from .registry import (
    RegistryEntry,
    _read_exclude,
    _write_exclude,
    load_registry,
    remove_immutable,
    sync_registry,
    update_registry,
)


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
