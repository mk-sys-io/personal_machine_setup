#!/usr/bin/env python3
"""Audit ~/Videos/keep/ against its keep.manifest (Phase 40-B).

keep/ is the sole backed-up media path (positional: mv a file in to back
it up). This checker is the preflight guard: lib/40-backup.py runs it
before any restic invocation so an undocumented, tampered, or over-budget
file can never become an immutable, quota-eating snapshot.

Usage: check_keep.py [keep-dir]   (default ~/Videos/keep)

Manifest format (keep.manifest, inside keep-dir): one line per file,
    sha256 path bytes reason
`reason` may contain spaces (split into at most 4 fields). The manifest
file itself is skipped during the audit.

Exit codes (mirroring lib/helpers/timeshift_excludes.py):
    0  pass (includes WARN: over 1 GiB but under the fail line)
    1  rejection (undocumented/stale/mismatched entry, over 3 GiB)
    2  usage error
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

WARN_BYTES = 1073741824  # 1 GiB — warn, still pass
FAIL_BYTES = 3221225472  # 3 GiB — reject (mirrors 80/90 alert/fail-loud)

MANIFEST_NAME = "keep.manifest"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fail(msg: str) -> int:
    print(f"check-keep: ERROR {msg}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a not in {"-h", "--help"}]
    if {"-h", "--help"} & set(argv[1:]):
        print(f"usage: {Path(argv[0]).name} [keep-dir]", file=sys.stderr)
        return 0
    if len(args) > 1:
        print(f"usage: {Path(argv[0]).name} [keep-dir]", file=sys.stderr)
        return 2

    keep = Path(args[0]).expanduser() if args else Path.home() / "Videos" / "keep"
    if not keep.is_dir():
        return fail(f"not a directory: {keep}")
    manifest = keep / MANIFEST_NAME

    on_disk = sorted(
        p for p in keep.iterdir() if p.is_file() and p.name != MANIFEST_NAME
    )
    if not on_disk and not manifest.is_file():
        print("check-keep: nothing to verify (empty, no manifest)")
        return 0
    if not manifest.is_file():
        return fail(f"non-empty keep/ with no manifest: {manifest}")

    entries: dict[str, tuple[str, int, str]] = {}
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return fail(f"cannot read {manifest}: {e}")
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split(None, 3)
        if len(parts) != 4:
            return fail(f"{manifest}:{lineno}: want 'sha256 path bytes reason'")
        digest, name, size_s, reason = parts
        try:
            size = int(size_s)
        except ValueError:
            return fail(f"{manifest}:{lineno}: bad bytes value: {size_s!r}")
        if not reason.strip():
            return fail(f"{manifest}:{lineno}: empty reason")
        entries[name] = (digest, size, reason)

    for path in on_disk:
        entry = entries.get(path.name)
        if entry is None:
            return fail(f"undocumented file (no manifest line): {path.name}")
        digest, size, _ = entry
        actual_size = path.stat().st_size
        if actual_size != size:
            return fail(
                f"{path.name}: size mismatch (manifest {size}, disk {actual_size})"
            )
        if sha256_of(path) != digest:
            return fail(f"{path.name}: sha256 mismatch (tampered or stale line)")

    for name in entries:
        if not (keep / name).is_file():
            return fail(f"stale manifest line (file missing): {name}")

    total = sum(p.stat().st_size for p in on_disk)
    if total > FAIL_BYTES:
        return fail(f"over budget: {total} bytes > {FAIL_BYTES} (3 GiB)")
    if total > WARN_BYTES:
        print(
            f"check-keep: WARN over 1 GiB: {total} bytes "
            f"({len(on_disk)} files verified)",
            file=sys.stderr,
        )
    else:
        print(f"check-keep: OK ({len(on_disk)} files, {total} bytes verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
