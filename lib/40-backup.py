#!/usr/bin/env python3
"""Standalone human backup CLI (Phase 40-D, with lib/40-restore.py).

Backup side of the two-stream design sharing lib/helpers/restic_common.py:
the livedata stream (excludes-filtered roots) and the vault stream
(--tag vault, explicit allowlist paths). Neither is trunk-wired.

Fast-forward default: every `backup` appends a new immutable snapshot
sharing blobs via dedup; never replace/mutate in place. Interactive
TTY runs show a top-level WILL/SKIPPED gate (`--dry-run --json`
aggregated by ~/*) and block on [y/N] before the real scan;
deletion review prompts only on `-` removals in the human CLI
(fast-forward otherwise, always fast-forward for --yes/timers/no-TTY/trunk).

Preflight runs lib/helpers/check_keep.py on ~/Videos/keep/ first
(backup-preflight-only; the standalone restore path never runs it).

Exit codes (0/2/1 convention):
    0  pass
    1  failure (preflight, restic error, checker rejection)
    2  usage error

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from preconditions import require_user  # noqa: E402
from restic_common import (  # noqa: E402
    base_args,
    build_repo_env,
    excludes_args,
    restic_binary,
    stat_excludes,
)

CHECK_KEEP = REPO_ROOT / "lib" / "helpers" / "check_keep.py"

# Livedata backup roots (parents passed so excludes filter contents —
# excludes never apply to explicitly-passed sources, only their contents).
LIVEDATA_ROOTS = ("~",)
# Vault-stream explicit allowlist (never a parent dir, never !-re-includes).
# Key-export dir lives outside the store dir so it never syncs to GitHub.
VAULT_PATHS = (".local/share/gopass", ".local/share/gpg-export")


def real_home() -> Path:
    import os

    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def run_check_keep(keep_dir: str | None) -> int:
    cmd = ["python3", str(CHECK_KEEP)]
    if keep_dir:
        cmd.append(keep_dir)
    r = subprocess.run(cmd)
    return r.returncode


def list_snapshot_diff(env: dict, tag: str | None) -> tuple[list[str], list[str]]:
    """Return (added, removed) paths of latest diff vs parent. Best-effort."""
    diff_cmd = ["restic", *base_args(), "diff", "latest^", "latest"]
    if tag:
        diff_cmd += ["--tag", tag]
    try:
        r = subprocess.run(diff_cmd, capture_output=True, text=True, timeout=300, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return [], []
    if r.returncode != 0:
        return [], []
    added = [ln[2:].strip() for ln in r.stdout.splitlines() if ln.startswith("+ ")]
    removed = [ln[2:].strip() for ln in r.stdout.splitlines() if ln.startswith("- ")]
    return added, removed


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:.0f}{unit}" if unit == "B" else f"{v:.1f}{unit}"
        v /= 1024
    return f"{n}B"


def _du_map(home: Path, names: list[str]) -> dict[str, int]:
    """Best-effort on-disk size per top-level name. Never raises."""
    if not names:
        return {}
    try:
        r = subprocess.run(
            ["du", "-sb", "--", *[str(home / n) for n in names[:100]]],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if r.returncode != 0:
        return {}
    out: dict[str, int] = {}
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        try:
            size = int(parts[0].strip())
        except ValueError:
            continue
        out[Path(parts[1].strip()).name] = size
    return out


def top_level_local(home: Path) -> list[str]:
    """Sorted top-level entries of $HOME. Best-effort, never raises."""
    try:
        return sorted(p.name for p in home.iterdir())
    except OSError:
        return []


def top_level_last(env: dict, tag: str | None, home: Path) -> set[str] | None:
    """Top-level ~/X names present in `latest`. None when unknown."""
    cmd = ["restic", *base_args(), "ls", "latest", "--json"]
    if tag:
        cmd += ["--tag", tag]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    home_s = str(home)
    tops: set[str] = set()
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        path = obj.get("path") if isinstance(obj, dict) else None
        if not isinstance(path, str) or not path:
            name = obj.get("name") if isinstance(obj, dict) else None
            if isinstance(name, str) and "/" not in name:
                tops.add(name)
            continue
        norm = path.rstrip("/") or "/"
        if norm == home_s or not norm.startswith(home_s + "/"):
            continue
        tops.add(norm[len(home_s) + 1 :].split("/", 1)[0])
    return tops


def print_tier_b(home: Path, last: set[str] | None) -> bool:
    """Print cheap local-vs-last overview. Return True if any NEW top-level.

    Dotfiles collapse to one count line (grouping by leading dot is
    presentation, not filter emulation); only IN-LAST or re-included
    (.ssh, .gnupg) dotfiles list individually. The note always points
    at WILL/SKIPPED below — never a self-referential "run --dry-run".
    """
    local = top_level_local(home)
    plain = [n for n in local if not n.startswith(".")]
    dotted = [n for n in local if n.startswith(".")]
    notable = sorted({n for n in dotted if n in (".ssh", ".gnupg") or (last is not None and n in last)})
    sizes = _du_map(home, plain[:30] + notable)
    print("40-backup: on-disk top-levels (NOT filter truth — excludes not applied):")
    has_new = False
    for name in plain[:30]:
        if last is None:
            status = "UNKNOWN-LAST"
        elif name in last:
            status = "IN-LAST"
        else:
            status = "NEW-ON-DISK"
            has_new = True
        print(f"  ~/{name}  {_fmt_bytes(sizes.get(name))}  {status}")
    if len(plain) > 30:
        print(f"  ... +{len(plain) - 30} more non-dot entries")
    for name in notable:
        if last is None:
            status = "UNKNOWN-LAST"
        elif name in last:
            status = "IN-LAST"
        else:
            status = "NEW-ON-DISK"
        print(f"  ~/{name}  {_fmt_bytes(sizes.get(name))}  {status}  (re-included dotfile)")
    hidden = len(dotted) - len(notable)
    print(f"  dotfiles: {len(dotted)} on disk, {hidden} hidden by collapse ($HOME/.* deny — see WILL/SKIPPED below)")
    print("40-backup: note: NEW-ON-DISK is vs last snapshot and unfiltered; WILL/SKIPPED below is the filter truth")
    return has_new


def _top_of(item: str, home_s: str) -> str | None:
    """Map an absolute scan item to its ~/X top-level.

    Return None for the scan root itself, its parents, or anything
    outside $HOME (scan-root noise, not content). Trailing slashes
    are stripped first so `/home/mike/` maps to the root, not `~/`.
    """
    norm = item.rstrip("/") or "/"
    if norm == home_s:
        return None
    if not norm.startswith(home_s + "/"):
        return None
    return "~/" + norm[len(home_s) + 1 :].split("/", 1)[0]


def run_dry_json(
    env: dict, home: Path, sources: list[str], restic_args: list[str], skip_if_unchanged: bool
) -> tuple[dict[str, dict[str, int]], set[str], set[str], set[str], dict, str | None]:
    """Run backup --dry-run --json, aggregate verbose_status by ~/X.

    Return (will, skipped_explicit, seen, unchanged_only, summary, error).
    will: top -> {files, bytes}. seen: every ~/X top the scanner
    mentioned (any action). skipped_explicit: excluded_item tops
    (often empty on restic 0.18 — callers derive the rest).
    error non-None means fail-closed.
    """
    cmd = ["restic", *base_args(), *excludes_args(), "backup", "--dry-run", "--json", "--verbose=2"]
    if skip_if_unchanged:
        cmd.append("--skip-if-unchanged")
    cmd += [*restic_args, *sources]
    child = dict(env)
    child["RESTIC_PROGRESS_FPS"] = "0.016"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=child)
    except subprocess.TimeoutExpired:
        return {}, set(), set(), set(), {}, "dry-run timed out"
    except OSError as exc:
        return {}, set(), set(), set(), {}, f"dry-run exec failed: {exc}"
    if r.returncode != 0:
        tail = ((r.stderr or "").strip().splitlines() or ["unknown restic error"])[-1]
        return {}, set(), set(), set(), {}, f"dry-run rc={r.returncode}: {tail}"
    home_s = str(home)
    will: dict[str, dict[str, int]] = {}
    skipped_explicit: set[str] = set()
    seen: set[str] = set()
    seen_unchanged: set[str] = set()
    summary: dict = {}
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        mtype = obj.get("message_type")
        if mtype == "summary":
            if isinstance(obj, dict):
                summary = obj
        elif mtype == "verbose_status":
            item = obj.get("item")
            if not isinstance(item, str) or not item:
                continue
            top = _top_of(item, home_s)
            if top is None:
                continue
            seen.add(top)
            action = obj.get("action")
            size = obj.get("data_size")
            size = size if isinstance(size, int) else 0
            if action in ("new", "modified"):
                e = will.setdefault(top, {"files": 0, "bytes": 0})
                e["files"] += 1
                e["bytes"] += size
            elif action == "unchanged":
                seen_unchanged.add(top)
        elif mtype == "excluded_item":
            item = obj.get("item")
            if isinstance(item, str) and item:
                top = _top_of(item, home_s)
                if top is not None:
                    skipped_explicit.add(top)
    unchanged_only = {t for t in seen_unchanged if t not in will}
    return will, skipped_explicit, seen, unchanged_only, summary, None


def derive_skipped(local: list[str], seen: set[str], explicit: set[str]) -> set[str]:
    """Tops on disk but absent from a successful scan, plus explicit ones.

    Scanned dirs always appear as scan items, so with rc=0 a local
    top the scanner never mentioned is definitionally excluded.
    Labeled unconditionally as excluded (eyeball-gate decision).
    """
    return {"~/" + n for n in local if "~/" + n not in seen} | explicit


def _is_dir_top(home: Path, top: str) -> bool:
    """True if a ~/X top is a directory on disk. Unresolvable → True."""
    if not top.startswith("~/"):
        return True
    try:
        return (home / top[2:]).is_dir()
    except OSError:
        return True


def _summary_counters(summary: dict, will: dict[str, dict[str, int]]) -> str:
    """Header counters from restic's own summary, computed sums as fallback."""
    files = sum(e["files"] for e in will.values())
    total = sum(e["bytes"] for e in will.values())
    parts = []
    fn, fc = summary.get("files_new"), summary.get("files_changed")
    if isinstance(fn, int) and isinstance(fc, int):
        parts.append(f"{fn} new + {fc} changed files")
    else:
        parts.append(f"{files} changed files")
    dn, dc = summary.get("dirs_new"), summary.get("dirs_changed")
    if isinstance(dn, int) and isinstance(dc, int):
        parts.append(f"{dn} new + {dc} changed dirs")
    return f"{', '.join(parts)}, {_fmt_bytes(total)} new"


def print_gate(home: Path, will: dict[str, dict[str, int]], skipped: set[str], unchanged: set[str], summary: dict) -> None:
    folders = sorted(t for t in will if _is_dir_top(home, t))
    files = sorted(t for t in will if not _is_dir_top(home, t))
    print(f"40-backup: WILL backup — {len(folders)} folders + {len(files)} files ({_summary_counters(summary, will)}):")
    for label, tops in (("folders", folders), ("files", files)):
        if not tops:
            continue
        print(f"  {label}:")
        for top in tops[:30]:
            e = will[top]
            print(f"    {top}  {_fmt_bytes(e['bytes'])}  {e['files']} changed")
        if len(tops) > 30:
            print(f"    ... +{len(tops) - 30} more {label}")
    if not will:
        print("  (none changed)")
    print("40-backup: SKIPPED by excludes.txt:")
    if skipped:
        # Non-dot majors listed; dotfiles collapse to a counter (same
        # treatment as Tier B — grouping by leading dot is presentation,
        # not filter emulation). du runs only over the shown majors.
        majors = sorted(t for t in skipped if not t.startswith("~/." ))
        dots = sum(1 for t in skipped if t.startswith("~/." ))
        sizes = _du_map(home, [t[2:] for t in majors[:30] if t.startswith("~/")])
        for top in majors[:30]:
            key = top[2:] if top.startswith("~/") else ""
            print(f"  {top}  {_fmt_bytes(sizes.get(key))}")
        if len(majors) > 30:
            print(f"  ... +{len(majors) - 30} more")
        if dots:
            print(f"  dotfiles: {dots} skipped ($HOME/.* deny)")
    else:
        print("  (none)")
    print("40-backup: UNCHANGED:")
    if unchanged:
        for top in sorted(unchanged)[:30]:
            print(f"  {top}")
        if len(unchanged) > 30:
            print(f"  ... +{len(unchanged) - 30} more")
    else:
        print("  (none)")
    total = summary.get("data_added")
    if isinstance(total, int):
        print(f"40-backup: total would-add: {_fmt_bytes(total)} (no snapshot written)")


def run_livedata_gate(env: dict, home: Path, sources: list[str], restic_args: list[str], skip_if_unchanged: bool) -> int:
    """Tier B + Tier A gate for livedata. 0 = gate displayed, 1 = fail-closed."""
    last = top_level_last(env, None, home)
    print_tier_b(home, last)
    will, skipped_explicit, seen, unchanged, summary, err = run_dry_json(env, home, sources, restic_args, skip_if_unchanged)
    if err is not None:
        print(f"40-backup: ERROR {err}", file=sys.stderr)
        return 1
    skipped = derive_skipped(top_level_local(home), seen, skipped_explicit)
    print_gate(home, will, skipped, unchanged, summary)
    return 0


def run_vault_gate(home: Path) -> int:
    print("40-backup: WILL backup (vault allowlist):")
    for p in VAULT_PATHS:
        print(f"  ~/{p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone human backup CLI (40-D)")
    p.add_argument("--tag", default="", help="snapshot tag (e.g. vault for the store stream)")
    p.add_argument("--keep-dir", default="", help="override ~/Videos/keep for the checker")
    p.add_argument("--skip-if-unchanged", action="store_true", help="opt out of fast-forward append")
    p.add_argument("--preview-only", action="store_true", help="snapshots --latest + diff + verdict, zero mutation")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="top-level WILL/SKIPPED gate via backup --dry-run --json, no snapshot written",
    )
    p.add_argument("--yes", action="store_true", help="non-interactive: fast-forward, no prompts")
    p.add_argument("--cleanup", action="store_true", help="also run per-tag forget --prune after backup")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_user("lib/40-backup.py", "python3 lib/40-backup.py"):
        return 1
    args = build_parser().parse_args(argv)
    tag = args.tag or None

    if restic_binary() is None:
        print("40-backup: ERROR restic binary missing — re-run ./install.py (phase 40-A)", file=sys.stderr)
        return 1
    try:
        stat_excludes()
    except (OSError, FileNotFoundError) as exc:
        print(f"40-backup: ERROR excludes file bad: {exc}", file=sys.stderr)
        return 1

    # Backup-preflight-only checker (restore never runs this).
    if not tag == "vault":
        rc = run_check_keep(args.keep_dir or None)
        if rc == 1:
            print("40-backup: ERROR check_keep rejected keep/ (undocumented/over-budget)", file=sys.stderr)
            return 1
        if rc == 2:
            print("40-backup: ERROR check_keep usage", file=sys.stderr)
            return 2

    home = real_home()
    # Vault allowlist existence is a local precondition — fail before any
    # secret touch (repo env reads gopass and can block on pinentry).
    if tag == "vault":
        missing = [p for p in VAULT_PATHS if not (home / p).exists()]
        if missing:
            for p in missing:
                print(f"40-backup: ERROR vault allowlist path missing: ~/{p} — run the Step-0b/V2 key export first", file=sys.stderr)
            return 1

    try:
        repo = build_repo_env()
    except (OSError, RuntimeError) as exc:
        print(f"40-backup: ERROR repo env: {exc}", file=sys.stderr)
        return 1

    if args.preview_only:
        cmd = ["restic", *base_args(), "snapshots", "--latest", "1"]
        if tag:
            cmd += ["--tag", tag]
        r = subprocess.run(cmd, env=repo.env)
        added, removed = list_snapshot_diff(repo.env, tag)
        print(f"40-backup: preview: +{len(added)} -{len(removed)} since parent")
        for rel in removed[:20]:
            print(f"  - {rel}")
        if removed and not args.yes:
            print("40-backup: worth-it verdict: review `-` removals before next backup")
        return 0

    home = real_home()
    if tag == "vault":
        sources = [str(home / p) for p in VAULT_PATHS]
        restic_args = ["--tag", "vault"]
    else:
        sources = [str(home)]
        restic_args = []
    if args.dry_run:
        # Deep gate: top-level WILL/SKIPPED, zero mutation. Ignores --cleanup.
        if tag == "vault":
            return run_vault_gate(home)
        return run_livedata_gate(repo.env, home, sources, restic_args, args.skip_if_unchanged)
    if not args.yes and sys.stdin.isatty():
        # Blocking eyeball gate before any real scan (double-scan cost accepted).
        if tag == "vault":
            run_vault_gate(home)
        elif run_livedata_gate(repo.env, home, sources, restic_args, args.skip_if_unchanged) != 0:
            return 1
        try:
            answer = input("40-backup: proceed with real backup scan? [y/N]: ").strip()
        except EOFError:
            answer = ""
        if answer.lower() != "y":
            print("40-backup: aborted before real scan (nothing written)")
            return 0
    cmd = ["restic", *base_args(), *excludes_args(), "backup", *restic_args, *sources]
    if args.skip_if_unchanged:
        cmd.append("--skip-if-unchanged")
    r = subprocess.run(cmd, env=repo.env)
    if r.returncode != 0:
        print(f"40-backup: ERROR backup rc={r.returncode}", file=sys.stderr)
        return 1

    if not tag:
        _, removed = list_snapshot_diff(repo.env, None)
        if removed and not args.yes and sys.stdin.isatty():
            print(f"40-backup: {len(removed)} paths vanished from this snapshot (recoverable from parents):")
            for rel in removed[:20]:
                print(f"  - {rel}")
            try:
                answer = input("40-backup: proceed knowing removals stay recoverable until forget prunes? [y/N]: ").strip()
            except EOFError:
                answer = ""
            if answer.lower() != "y":
                print("40-backup: snapshot kept (fast-forward); re-run with --yes to silence", file=sys.stderr)
                return 0

    if args.cleanup:
        forget = ["restic", *base_args(), "forget", "--keep-daily", "7",
                  "--keep-weekly", "4", "--keep-monthly", "6",
                  "--group-by", "host,paths,tags", "--prune"]
        if tag:
            forget += ["--tag", tag]
        r = subprocess.run(forget, env=repo.env)
        if r.returncode != 0:
            print(f"40-backup: ERROR forget rc={r.returncode}", file=sys.stderr)
            return 1
    print("40-backup: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
