#!/usr/bin/env python3
"""Idempotently inject exclude patterns into timeshift.json.

Replaces the single jq call site (60-ark.sh deploy_timeshift): jq is not
in the stage-0 set, so the JSON edit now needs nothing beyond stdlib.

Usage: timeshift_excludes.py <timeshift.json> <pattern> [<pattern> ...]

Merges the patterns into the top-level "exclude" list (deduplicated,
sorted — mirroring jq's `unique`). Atomic replace: renders to a temp file
in the same directory, then os.replace. Owner/mode are left to the caller
(60-ark.sh chowns root:root + chmods 644 after a successful run).

Exit codes:
    0  injected (or already present — idempotent no-op)
    1  failure (missing file, invalid JSON, write failed)
    2  skipped (usage error)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 3 or {"-h", "--help"} & set(argv[1:]):
        print(f"usage: {Path(argv[0]).name} <timeshift.json> <pattern> [...]",
              file=sys.stderr)
        return 2 if len(argv) < 3 else 0

    tsjson = Path(argv[1])
    patterns = argv[2:]
    try:
        data = json.loads(tsjson.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"timeshift-excludes: ERROR not found: {tsjson}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as e:
        print(f"timeshift-excludes: ERROR cannot read {tsjson}: {e}",
              file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print(f"timeshift-excludes: ERROR top-level JSON is not an object: {tsjson}",
              file=sys.stderr)
        return 1

    existing = data.get("exclude") or []
    if not isinstance(existing, list):
        print(f"timeshift-excludes: ERROR 'exclude' is not a list: {tsjson}",
              file=sys.stderr)
        return 1
    data["exclude"] = sorted({str(x) for x in existing} | set(patterns))

    try:
        fd, tmp = tempfile.mkstemp(dir=tsjson.parent,
                                   prefix=".timeshift.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, tsjson)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError as e:
        print(f"timeshift-excludes: ERROR cannot write {tsjson}: {e}",
              file=sys.stderr)
        return 1
    print(f"timeshift-excludes: excludes merged into {tsjson}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
