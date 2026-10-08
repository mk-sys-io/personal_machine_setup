#!/usr/bin/env python3
"""backup-preview — thin PATH CLI for the 40-D worth-it verdict.

Delegates to lib/40-backup.py (canonical owner) on restic_common.py
shared plumbing: `snapshots --latest` + diff + worth-it verdict, zero
mutation. Deployed to ~/.local/bin/backup-preview via the make dev
tools loop (same precedent as tools/vm.py -> lib/65-vm.py).

Usage:
    backup-preview [--tag vault]

Stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path


def get_repo_root() -> Path:
    deployed = Path.home() / ".config" / "linux_setup" / "config.txt"
    if deployed.is_file():
        for line in deployed.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("REPO_ROOT="):
                _, _, val = s.partition("=")
                val = val.strip().strip("'\"")
                if val:
                    return Path(val)
    # Fall back to the source tree when run from the repo.
    return Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    import subprocess

    repo_root = get_repo_root()
    owner = repo_root / "lib" / "40-backup.py"
    if not owner.is_file():
        print(f"error: {owner} not found", file=sys.stderr)
        return 1
    cmd = ["python3", str(owner), "--preview-only", "--yes", *argv[1:]]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
