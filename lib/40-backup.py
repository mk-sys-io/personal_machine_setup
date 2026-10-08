#!/usr/bin/env python3
"""Standalone human backup CLI (Phase 40-D, with lib/40-restore.py).

Backup side of the two-stream design sharing lib/helpers/restic_common.py:
the livedata stream (excludes-filtered roots) and the vault stream
(--tag vault, explicit allowlist paths). Neither is trunk-wired.

Fast-forward default: every `backup` appends a new immutable snapshot
sharing blobs via dedup; never replace/mutate in place. Deletion review
prompts only on `-` removals in the human CLI (fast-forward otherwise,
always fast-forward for timers/no-TTY/trunk).

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
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))

from preconditions import require_user  # noqa: E402
from restic_common import (  # noqa: E402
    base_args,
    build_repo_env,
    restic_binary,
    stat_excludes,
)

CHECK_KEEP = REPO_ROOT / "lib" / "helpers" / "check_keep.py"

# Livedata backup roots (parents passed so excludes filter contents —
# excludes never apply to explicitly-passed sources, only their contents).
LIVEDATA_ROOTS = ("~",)
# Vault-stream explicit allowlist (never a parent dir, never !-re-includes).
VAULT_PATHS = (".local/share/gopass",)


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone human backup CLI (40-D)")
    p.add_argument("--tag", default="", help="snapshot tag (e.g. vault for the store stream)")
    p.add_argument("--keep-dir", default="", help="override ~/Videos/keep for the checker")
    p.add_argument("--skip-if-unchanged", action="store_true", help="opt out of fast-forward append")
    p.add_argument("--preview-only", action="store_true", help="snapshots --latest + diff + verdict, zero mutation")
    p.add_argument("--dry-run", action="store_true", help="restic --dry-run, no snapshot written")
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
    cmd = ["restic", *base_args(), "backup", *restic_args, *sources]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.skip_if_unchanged:
        cmd.append("--skip-if-unchanged")
    r = subprocess.run(cmd, env=repo.env)
    if r.returncode != 0:
        print(f"40-backup: ERROR backup rc={r.returncode}", file=sys.stderr)
        return 1

    if not args.dry_run and not tag:
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
