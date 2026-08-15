"""Blocklist management operations -- add/block/unblock/remove/lookup/exempt.

Operates on the new sources.json + focused/upstream/ layout.  All mutable
files are edited repo-first (ARK_REPO_ETC_PATH), then synced to live and
regenerated.  The registry module has been removed; all upstream tracking
is driven by sources.json.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil

import opslog

from ._config import (
    CUSTOM_FILE,
    CUSTOM_HEADER,
    CUSTOM_REPO_FILE,
    OUTPUT_FILE,
    REPO_DOMAINS_DIR,
    SOURCES_FILE,
    UPSTREAM_DIR,
    BlocklistError,
    _normalize_line,
    _read_domains,
    _read_exceptions,
    _read_exclude,
    _repo_file,
    _valid_domain,
    _write_exceptions,
    _write_exclude,
    _write_repo_file,
    ensure_domains_dir,
    ensure_upstream_dir,
)
from .generate import generate

# -- helpers ------------------------------------------------------------------


def load_sources() -> list[dict[str, object]]:
    """Load sources.json. Returns empty list if missing."""
    if not os.path.isfile(SOURCES_FILE):
        return []
    with open(SOURCES_FILE) as f:
        data = json.load(f)
    return data.get("sources", [])


def save_sources(sources: list[dict[str, object]]) -> None:
    """Write sources.json atomically."""
    data = {"version": 1, "sources": sources}
    tmp = SOURCES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, SOURCES_FILE)


def _upstream_files() -> list[str]:
    """Return sorted list of upstream .txt files."""
    ensure_upstream_dir()
    return sorted(glob.glob(os.path.join(UPSTREAM_DIR, "*.txt")))


def _source_file_path(source_id: str) -> str:
    """Return the upstream file path for a source ID."""
    return os.path.join(UPSTREAM_DIR, f"{source_id}.txt")


def _count_file_domains(filepath: str) -> int:
    """Count non-comment, non-empty lines in a file."""
    count = 0
    with open(filepath) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
    return count


def _confirm(prompt: str, *, default: bool = False) -> bool:
    """Prompt for confirmation. Default decides the bare-Enter answer."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _snapshot(rel_path: str) -> None:
    """Snapshot a repo file to <name>.bak before a mutation (rollback point)."""
    repo = f"{REPO_DOMAINS_DIR}/{rel_path}"
    if os.path.isfile(repo):
        shutil.copy2(repo, repo + ".bak")


# -- group/banner helpers -----------------------------------------------------


_SECTION_RE = re.compile(r"^#\s*[-=]{3,}\s*(.*?)\s*[-=]{3,}\s*$")
_GROUP_RE = re.compile(r"^#\s+(.+?)\s*$")


def _groups(lines: list[str]) -> list[str]:
    """Return group banner names, excluding section dividers."""
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        if _SECTION_RE.match(stripped):
            continue
        m = _GROUP_RE.match(stripped)
        if m and not m.group(1).startswith("-"):
            result.append(m.group(1))
    return result


def _banner_index(lines: list[str], name: str) -> int | None:
    """Return the line index of the banner named ``name`` (case-insensitive)."""
    target = name.strip().lower()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _SECTION_RE.match(stripped):
            continue
        m = _GROUP_RE.match(stripped)
        if m and not m.group(1).startswith("-") and m.group(1).lower() == target:
            return i
    return None


def _group_domain_indices(lines: list[str], name: str) -> list[int] | None:
    """Return indices of domain lines under banner ``name``.

    Returns None if the banner is missing; [] if it has no domains.
    """
    banner_idx = _banner_index(lines, name)
    if banner_idx is None:
        return None
    indices: list[int] = []
    for j in range(banner_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("#"):
            break
        if stripped and _valid_domain(_normalize_line(stripped)):
            indices.append(j)
    return indices


def _resolve_targets(
    lines: list[str], target: str | None, group: str | None
) -> list[tuple[int, str, bool]] | None:
    """Return (index, domain, is_active) tuples for matching lines.

    Raises BlocklistError for an unknown group. Returns None when a domain
    is not found; [] when a known group has no domains.
    """
    if group is not None:
        indices = _group_domain_indices(lines, group)
        if indices is None:
            available = "\n  ".join(_groups(lines)) or "(none)"
            raise BlocklistError(
                f"Group '{group}' not found. Available groups:\n  {available}"
            )
        return [
            (i, _normalize_line(lines[i]), not lines[i].lstrip().startswith("#"))
            for i in indices
        ]
    if target is not None:
        found: list[tuple[int, str, bool]] = []
        for i, line in enumerate(lines):
            norm = _normalize_line(line)
            if norm == target and _valid_domain(norm):
                found.append((i, norm, not line.lstrip().startswith("#")))
        return found or None
    return None


def _insert_domains(lines: list[str], group_name: str, added: list[str]) -> list[str]:
    """Insert domains under an existing banner, or append a new banner group."""
    idx = _banner_index(lines, group_name)
    new_lines = list(lines)
    if idx is not None:
        k = idx + 1
        while k < len(new_lines) and new_lines[k].strip() and not new_lines[k].strip().startswith("#"):
            k += 1
        new_lines[k:k] = [f"{d}\n" for d in added]
        return new_lines
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    new_lines.append("\n")
    new_lines.append(f"# {group_name}\n")
    new_lines.extend(f"{d}\n" for d in added)
    return new_lines


def _prune_empty_group(lines: list[str], name: str) -> list[str]:
    """Drop a banner (and one preceding blank) once it has no domain lines left."""
    banner_idx = _banner_index(lines, name)
    if banner_idx is None:
        return lines
    for j in range(banner_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("#"):
            break
        if stripped and _valid_domain(_normalize_line(stripped)):
            return lines
    result = list(lines)
    del result[banner_idx]
    if banner_idx > 0 and not result[banner_idx - 1].strip():
        del result[banner_idx - 1]
    return result


def _resolve_add_group(lines: list[str], group: str | None, yes: bool) -> str:
    """Pick the target group banner for add: --group, default, or interactive."""
    if group:
        name = group.strip()
        if _banner_index(lines, name) is not None or yes:
            return name
        if _confirm(f"Group '{name}' does not exist. Create it?", default=False):
            return name
        raise BlocklistError("Group creation cancelled.")
    if yes:
        return "Miscellaneous"
    existing = _groups(lines)
    opslog.info("\nGroup (existing or new):")
    for i, g in enumerate(existing, 1):
        opslog.info("  %d. %s", i, g)
    opslog.info("  %d. Create new...", len(existing) + 1)
    raw = input("\nSelect number or type a name: ").strip()
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= len(existing):
            return existing[n - 1]
        if n == len(existing) + 1:
            name = input("New group name: ").strip()
            if name:
                return name
            raise BlocklistError("No group name given.")
        raise BlocklistError(f"Invalid selection: {n}")
    if raw:
        return raw
    raise BlocklistError("No group selected.")


# -- purge --------------------------------------------------------------------


def _build_purge_list() -> list[tuple[int, dict[str, object], int]]:
    """Build numbered list of upstream sources for purge selection.

    Returns list of (position, source_entry, domain_count) tuples.
    """
    sources = load_sources()
    purge_list: list[tuple[int, dict[str, object], int]] = []
    for idx, src in enumerate(sources, 1):
        sid = str(src.get("id", ""))
        filepath = _source_file_path(sid)
        if not os.path.isfile(filepath):
            continue
        count = _count_file_domains(filepath)
        purge_list.append((idx, src, count))
    return purge_list


def _prompt_selection(
    purge_list: list[tuple[int, dict[str, object], int]],
) -> list[dict[str, object]]:
    """Display numbered list and prompt user to select entries to purge."""
    opslog.info("Available upstream sources:\n")
    opslog.info("  %3s  %-40s %-50s %10s", "#", "ID", "URL", "DOMAINS")
    for pos, src, count in purge_list:
        opslog.info(
            "  %3d  %-40s %-50s %10s",
            pos,
            src.get("id", ""),
            src.get("url", ""),
            f"{count:,}",
        )

    raw: str = input("\nEnter number(s) to purge (e.g. 1 3): ").strip()
    if not raw:
        raise BlocklistError("Purge cancelled.")

    selected: list[dict[str, object]] = []
    for token in raw.split():
        if not token.isdigit():
            raise BlocklistError(f"invalid position: {token}")
        num = int(token)
        matches = [s for p, s, _ in purge_list if p == num]
        if not matches:
            raise BlocklistError(f"invalid position: {num}")
        selected.append(matches[0])

    return selected


def _confirm_purge(entries: list[dict[str, object]]) -> bool:
    """Show confirmation prompt for purge operation. Returns True if confirmed."""
    opslog.info("\nAbout to purge:")
    for src in entries:
        sid = str(src.get("id", ""))
        filepath = _source_file_path(sid)
        count = _count_file_domains(filepath) if os.path.isfile(filepath) else 0
        opslog.info("  %s (%s) -- %s domains", sid, src.get("url", ""), f"{count:,}")

    return _confirm("\nProceed?", default=False)


def purge(
    *,
    all_domains: bool = False,
    targets: list[str] | None = None,
    yes: bool = False,
) -> None:
    """Remove downloaded upstream source(s) with interactive selection."""
    purge_list = _build_purge_list()

    if not purge_list:
        opslog.info("No upstream sources to purge.")
        return

    selected: list[dict[str, object]] = []

    if all_domains:
        selected = [src for _, src, _ in purge_list]
    elif targets:
        for target in targets:
            if target.isdigit():
                num = int(target)
                matches = [s for p, s, _ in purge_list if p == num]
                if not matches:
                    raise BlocklistError(f"invalid position: {num}")
                selected.append(matches[0])
            else:
                matches = [s for _, s, _ in purge_list if str(s.get("id", "")).startswith(target)]
                if not matches:
                    raise BlocklistError(f"no source with id prefix '{target}'")
                if len(matches) > 1:
                    raise BlocklistError(
                        f"ambiguous id prefix '{target}' -- matches {len(matches)} entries"
                    )
                selected.append(matches[0])
    else:
        selected = _prompt_selection(purge_list)

    if not yes:
        if not _confirm_purge(selected):
            opslog.info("Purge cancelled.")
            return

    selected_ids = {str(s.get("id", "")) for s in selected}

    for sid in selected_ids:
        filepath = _source_file_path(sid)
        if os.path.isfile(filepath):
            os.remove(filepath)
            opslog.info("Deleted: %s.txt", sid)

    sources = load_sources()
    sources = [s for s in sources if str(s.get("id", "")) not in selected_ids]
    save_sources(sources)
    opslog.info("Removed %d source(s) from sources.json.", len(selected_ids))

    if os.path.isfile(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)
        opslog.info("Deleted: %s", os.path.basename(OUTPUT_FILE))

    opslog.info("Regenerating %s...", os.path.basename(OUTPUT_FILE))
    generate()


# -- add ----------------------------------------------------------------------


def add(
    domains: list[str],
    *,
    group: str | None = None,
    file: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
) -> None:
    """Add domain(s) to the custom blocklist under a group banner."""
    clean: list[str] = [d.lower().strip() for d in domains]

    if file:
        try:
            with open(file) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        clean.append(stripped.lower())
        except OSError as e:
            raise BlocklistError(f"Cannot read {file}: {e}") from e

    invalid = [d for d in clean if not _valid_domain(d)]
    if invalid:
        raise BlocklistError(f"Invalid domain format: {invalid[0]}")
    if not clean:
        raise BlocklistError("No domains to add.")

    lines = _repo_file("blocklist-custom.txt")
    active = {_normalize_line(line) for line in lines if not line.lstrip().startswith("#")}
    commented = {
        _normalize_line(line)
        for line in lines
        if line.lstrip().startswith("#") and _valid_domain(_normalize_line(line))
    }

    group_name = _resolve_add_group(lines, group, yes)

    added: list[str] = []
    for domain in clean:
        if domain in active:
            opslog.info("Already in blocklist: %s", domain)
            continue
        if domain in commented:
            if yes or _confirm(
                f"Domain exists but is commented. Uncomment '{domain}'?",
                default=False,
            ):
                block(domain, yes=True, dry_run=dry_run)
            else:
                opslog.info("Skipped: %s", domain)
            continue
        added.append(domain)

    if not added:
        return

    if dry_run:
        for d in added:
            opslog.info("  Would add: %s -> # %s", d, group_name)
        return

    _snapshot("blocklist-custom.txt")
    _write_repo_file("blocklist-custom.txt", _insert_domains(lines, group_name, added))

    exc = _read_exclude()
    if set(added) & exc:
        _snapshot("blocklist-exclude.txt")
        for d in added:
            exc.discard(d)
        _write_exclude(exc)

    for d in added:
        opslog.info("Added: %s -> # %s", d, group_name)
    generate()


# -- block / unblock / remove -------------------------------------------------


def block(
    domain: str | None = None,
    *,
    group: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
) -> None:
    """Uncomment a domain/group in custom + remove from exclude -> blocked."""
    if domain is None and group is None:
        raise BlocklistError("Provide a domain or --group")

    target: str | None = domain.lower().strip() if domain else None
    if target is not None and not _valid_domain(target):
        raise BlocklistError(f"Invalid domain format: {target}")

    lines = _repo_file("blocklist-custom.txt")
    targets = _resolve_targets(lines, target, group)
    if targets is None:
        opslog.info("Domain not found: %s", target)
        return
    if not targets:
        opslog.info("No entries found for group: %s", group)
        return

    if target is not None:
        current_ex = _read_exceptions()
        if target in current_ex:
            if dry_run:
                opslog.info(
                    "  Would prompt to remove exemption: %s (default remove)", target
                )
            elif not yes and not _confirm(
                f"{target} is currently exempted and would stay allowed. "
                "Remove the exemption so the block applies?",
                default=True,
            ):
                opslog.info("Block aborted.")
                return
            if not dry_run:
                _snapshot("blocklist-exceptions.txt")
                _write_exceptions([e for e in current_ex if e != target])
        for e in sorted(e for e in current_ex if e.endswith("." + target)):
            opslog.info("  Blocked: %s -- still allowed: %s", target, e)

    if all(active for _, _, active in targets):
        opslog.info("Already blocked: %s", target or group)
        return

    if not yes and not dry_run and not _confirm(
        f"Block '{target or group}'?", default=False
    ):
        opslog.info("Block cancelled.")
        return

    if dry_run:
        for _, norm, _ in targets:
            opslog.info("  Would uncomment: %s", norm)
        return

    _snapshot("blocklist-custom.txt")
    for i, norm, _ in targets:
        lines[i] = f"{norm}\n"
    _write_repo_file("blocklist-custom.txt", lines)

    discard = {norm for _, norm, _ in targets}
    exc = _read_exclude()
    if discard & exc:
        _snapshot("blocklist-exclude.txt")
        for d in discard:
            exc.discard(d)
        _write_exclude(exc)

    scope = f" ({len(targets)} entries)" if len(targets) > 1 else ""
    opslog.info("Blocked: %s%s", target or group, scope)
    generate()


def unblock(
    domain: str | None = None,
    *,
    group: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
) -> None:
    """Comment out a domain/group in custom + add to exclude -> allowed."""
    if domain is None and group is None:
        raise BlocklistError("Provide a domain or --group")

    target: str | None = domain.lower().strip() if domain else None
    if target is not None and not _valid_domain(target):
        raise BlocklistError(f"Invalid domain format: {target}")

    lines = _repo_file("blocklist-custom.txt")
    targets = _resolve_targets(lines, target, group)

    if targets is None:
        parent = _covering_parent(target) if target is not None else None
        if target is not None and parent is not None:
            opslog.info(
                "  %s is not in the custom list but is covered by parent wildcard "
                "%s -- routing to exemption.",
                target,
                parent,
            )
            exempt_add(target, yes=yes, dry_run=dry_run)
        else:
            opslog.info("Domain not found: %s", target)
        return
    if not targets:
        opslog.info("No entries found for group: %s", group)
        return

    if all(not active for _, _, active in targets):
        opslog.info("Already unblocked: %s", target or group)
        return

    if not yes and not dry_run and not _confirm(
        f"Unblock '{target or group}'? (comment out + exclude)", default=False
    ):
        opslog.info("Unblock cancelled.")
        return

    if dry_run:
        for _, norm, _ in targets:
            opslog.info("  Would comment out: %s", norm)
        return

    _snapshot("blocklist-custom.txt")
    for i, norm, _ in targets:
        lines[i] = f"# {norm}\n"
    _write_repo_file("blocklist-custom.txt", lines)

    add_list = {norm for _, norm, _ in targets}
    exc = _read_exclude()
    if add_list - exc:
        _snapshot("blocklist-exclude.txt")
        exc.update(add_list)
        _write_exclude(exc)

    scope = f" ({len(targets)} entries)" if len(targets) > 1 else ""
    opslog.info("Unblocked: %s%s", target or group, scope)
    generate()


def remove(
    domain: str | None = None,
    *,
    group: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
) -> None:
    """Delete a domain/group from custom + add to exclude."""
    if domain is None and group is None:
        raise BlocklistError("Provide a domain or --group")

    target: str | None = domain.lower().strip() if domain else None
    if target is not None and not _valid_domain(target):
        raise BlocklistError(f"Invalid domain format: {target}")

    lines = _repo_file("blocklist-custom.txt")
    targets = _resolve_targets(lines, target, group)
    if targets is None:
        opslog.info("Domain not found: %s", target)
        return
    if not targets:
        opslog.info("No entries found for group: %s", group)
        return

    scope = f" and {len(targets) - 1} other entr{'ies' if len(targets) > 2 else 'y'}"
    if not yes and not dry_run and not _confirm(
        f"Permanently delete '{target or group}'{'' if len(targets) == 1 else scope}?",
        default=False,
    ):
        opslog.info("Remove cancelled.")
        return

    if dry_run:
        for _, norm, _ in targets:
            opslog.info("  Would delete: %s", norm)
        return

    remove_idx = {i for i, _, _ in targets}
    keep = [line for i, line in enumerate(lines) if i not in remove_idx]
    if group is not None:
        keep = _prune_empty_group(keep, group)
    if not any(stripped and not stripped.startswith("#") for stripped in map(str.strip, keep)):
        keep = [CUSTOM_HEADER]

    _snapshot("blocklist-custom.txt")
    _write_repo_file("blocklist-custom.txt", keep)

    add_list = {norm for _, norm, _ in targets}
    exc = _read_exclude()
    if add_list - exc:
        _snapshot("blocklist-exclude.txt")
        exc.update(add_list)
        _write_exclude(exc)

    scope_txt = f" ({len(targets)} entries)" if len(targets) > 1 else ""
    opslog.info("Deleted: %s%s", target or group, scope_txt)
    generate()


# -- exempt -------------------------------------------------------------------


def _covering_parent(domain: str) -> str | None:
    """Return the most specific parent wildcard covering ``domain``.

    Searches the custom list + all upstream files: ``domain`` is covered by
    ``P`` when ``domain == P`` or ``domain.endswith('.' + P)``.
    """
    candidates: list[str] = []
    if os.path.isfile(CUSTOM_REPO_FILE):
        candidates.extend(_read_domains(CUSTOM_REPO_FILE))
    for filepath in _upstream_files():
        candidates.extend(_read_domains(filepath))
    matches = [c for c in candidates if domain == c or domain.endswith("." + c)]
    return max(matches, key=len) if matches else None


def exempt_add(domain: str, *, yes: bool = False, dry_run: bool = False) -> None:
    """Add a wildcard exception (server=/domain/#) for a covered domain."""
    d = domain.lower().strip()
    if not _valid_domain(d):
        raise BlocklistError(f"Invalid domain format: {d}")

    current = _read_exceptions()
    if d in current:
        opslog.info("Already exempted: %s", d)
        return

    parent = _covering_parent(d)
    if parent is None:
        raise BlocklistError(
            f"No parent wildcard covers {d} -- exception would have no effect"
        )

    if not yes and not dry_run and not _confirm(
        f"Exempt {d} (overrides {parent} wildcard)?", default=False
    ):
        opslog.info("Exempt add cancelled.")
        return

    if dry_run:
        opslog.info("  Would add exception: %s (overrides %s)", d, parent)
        return

    _snapshot("blocklist-exceptions.txt")
    _write_exceptions(current + [d])
    opslog.info("Exempted: %s (overrides %s)", d, parent)
    generate()


def exempt_remove(domain: str, *, yes: bool = False, dry_run: bool = False) -> None:
    """Remove a wildcard exception."""
    d = domain.lower().strip()
    if not _valid_domain(d):
        raise BlocklistError(f"Invalid domain format: {d}")

    current = _read_exceptions()
    if d not in current:
        opslog.info("Not exempted: %s", d)
        return

    if not yes and not dry_run and not _confirm(
        f"Remove exemption for {d}?", default=False
    ):
        opslog.info("Exempt remove cancelled.")
        return

    if dry_run:
        opslog.info("  Would remove exception: %s", d)
        return

    _snapshot("blocklist-exceptions.txt")
    _write_exceptions([e for e in current if e != d])
    opslog.info("Removed exemption: %s", d)
    generate()


def exempt_list() -> None:
    """Show each exemption and the parent wildcard it overrides."""
    current = _read_exceptions()
    if not current:
        opslog.info("No exemptions.")
        return
    for e in current:
        parent = _covering_parent(e)
        suffix = f" (overrides {parent})" if parent else ""
        opslog.info("  %s%s", e, suffix)


# -- lookup -------------------------------------------------------------------


def lookup(domain: str) -> None:
    """Show blocked/exempt status for a domain. Exemption wins."""
    d = domain.lower().strip()
    if not _valid_domain(d):
        raise BlocklistError(f"Invalid domain format: {d}")

    if d in _read_exceptions():
        parent = _covering_parent(d)
        suffix = f" (unblocked from {parent} wildcard)" if parent else ""
        opslog.info("EXEMPT  blocklist-exceptions.txt%s", suffix)
        return

    if os.path.isfile(CUSTOM_REPO_FILE) and d in set(_read_domains(CUSTOM_REPO_FILE)):
        opslog.info("BLOCKED  blocklist-custom.txt (your list)")
        return

    for filepath in _upstream_files():
        if d in set(_read_domains(filepath)):
            opslog.info(
                "BLOCKED  upstream/%s (system, not editable)",
                os.path.basename(filepath),
            )
            return

    parent = _covering_parent(d)
    if parent is not None:
        opslog.info("BLOCKED  via %s (parent wildcard)", parent)
        return

    opslog.info("NOT FOUND")


# -- search -------------------------------------------------------------------


def search(pattern: str) -> None:
    """Regex search across all blocklist files."""
    try:
        compiled: re.Pattern[str] = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise BlocklistError(f"Invalid regex: {e}") from e

    files: list[str] = _upstream_files()
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


# -- stats --------------------------------------------------------------------


def stats() -> None:
    """Show domain counts per file and total."""
    ensure_domains_dir()

    upstream = _upstream_files()
    has_custom = os.path.isfile(CUSTOM_FILE)

    if not upstream and not has_custom:
        opslog.info("No blocklist files found.")
        return

    total: int = 0
    for filepath in upstream:
        filename: str = os.path.basename(filepath)
        count = _count_file_domains(filepath)
        total += count
        opslog.info("  %s: %d domains", filename, count)

    if has_custom:
        count = _count_file_domains(CUSTOM_FILE)
        total += count
        opslog.info("  %s: %d domains", os.path.basename(CUSTOM_FILE), count)

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
    opslog.info(
        "\n  Total: %d domains across %d file(s)", total, len(upstream) + (1 if has_custom else 0)
    )


# -- status counts ------------------------------------------------------------


def blocklist_counts() -> dict[str, int]:
    """Return blocklist counts for `netmgr status` (repo as source of truth)."""
    custom = (
        _count_file_domains(CUSTOM_REPO_FILE)
        if os.path.isfile(CUSTOM_REPO_FILE)
        else _count_file_domains(CUSTOM_FILE)
    )
    upstream_files = _upstream_files()
    upstream = sum(_count_file_domains(f) for f in upstream_files)
    generated = 0
    if os.path.isfile(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            generated = sum(1 for line in f if line.startswith("local=/"))
    return {
        "custom": custom,
        "upstream": upstream,
        "sources": len(upstream_files),
        "exemptions": len(_read_exceptions()),
        "generated": generated,
    }


# -- verify -------------------------------------------------------------------


def verify() -> None:
    """Verify blocklist file integrity and status."""
    ensure_domains_dir()

    opslog.info("File verification:")
    upstream = _upstream_files()
    has_custom = os.path.isfile(CUSTOM_FILE)

    if not upstream and not has_custom:
        opslog.info("  No blocklist files found.")

    for filepath in upstream:
        filename: str = os.path.basename(filepath)
        exists: bool = os.path.isfile(filepath)
        if exists:
            size: int = os.path.getsize(filepath)
            status: str = "OK" if size > 0 else "EMPTY"
            opslog.info("  %s: %s (%d bytes)", filename, status, size)
        else:
            opslog.info("  %s: MISSING", filename)

    if has_custom:
        size = os.path.getsize(CUSTOM_FILE)
        status = "OK" if size > 0 else "EMPTY"
        opslog.info("  %s: %s (%d bytes)", os.path.basename(CUSTOM_FILE), status, size)

    if os.path.isfile(OUTPUT_FILE):
        size = os.path.getsize(OUTPUT_FILE)
        status = "OK" if size > 0 else "EMPTY"
        opslog.info("  %s: %s (%d bytes)", os.path.basename(OUTPUT_FILE), status, size)
    else:
        opslog.info("  %s: MISSING (run 'generate')", os.path.basename(OUTPUT_FILE))

    if os.path.isfile(SOURCES_FILE):
        sources = load_sources()
        opslog.info("  Sources: %d entry/entries", len(sources))
    else:
        opslog.info("  Sources: not initialized")

    exclude_count: int = len(_read_exclude())
    opslog.info("  Exclude list: %d domain(s)", exclude_count)
