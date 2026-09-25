#!/usr/bin/env python3
"""Render gomplate templates in-place, continuing on per-file failures.

Walks the render paths given on argv, renders every file containing
gomplate Env markers via gomplate, and atomically replaces the original
file only when the render succeeds. A failing render never touches the
original (the partial gomplate output is written to a temp file in the
same directory and discarded), so a broken substitution leaves the raw
template intact for inspection and re-deploy.

Per-file gomplate failures do not stop the run: every file is attempted,
all failures are collected, and the exit code reports the outcome.

Exit codes:
    0  every template rendered successfully
    1  one or more templates failed to render
    2  no render paths given on argv
    other  unhandled crash in this script (caller aborts deploy)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator

MARKER = "{{ .Env."

# Gomplate stderr is a single JSON line with an "err" field; the err text
# embeds template name, line:col, and the failed variable, e.g.:
#   template: /opt/ark/scripts/cask/lib.py:29:20: executing ".../cask/lib.py"
#   at <.Env.USERNAME>: map has no entry for key "USERNAME"
_ERR_RE = re.compile(
    r"template: (?P<file>.+?):(?P<line>\d+):(?P<col>\d+): "
    r"executing .*? at <\.Env\.(?P<var>[^>]+)>\s*:\s*(?P<msg>.+)$"
)


def iter_files(render_paths: list[str]) -> Iterator[str]:
    """Yield every regular file under render paths (paths may be files)."""
    for root in render_paths:
        if os.path.isfile(root):
            yield root
        elif os.path.isdir(root):
            for dirpath, _dirs, names in os.walk(root):
                for name in names:
                    yield os.path.join(dirpath, name)


def find_templates(render_paths: list[str]) -> list[tuple[str, int]]:
    """Return (path, marker_count) for every file containing markers."""
    found: list[tuple[str, int]] = []
    for path in iter_files(render_paths):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        count = content.count(MARKER)
        if count > 0:
            found.append((path, count))
    return found


def parse_gomplate_error(stderr: bytes) -> str:
    """Extract a readable file:line:col + variable summary from gomplate."""
    try:
        err = json.loads(stderr.decode(errors="replace")).get("err", "")
    except (json.JSONDecodeError, UnicodeDecodeError):
        err = stderr.decode(errors="replace").strip()
    match = _ERR_RE.search(err)
    if match:
        return (
            f"{match.group('file')}:{match.group('line')}:{match.group('col')} "
            f"variable <.Env.{match.group('var')}>: {match.group('msg')}"
        )
    return err.strip() or "gomplate failed"


def render_file(path: str) -> tuple[bool, str]:
    """Render one template via gomplate. Returns (ok, error detail).

    Renders to a temp file in the same directory so a failed render can
    never corrupt the original; on success the original's permission bits
    are preserved and the temp file replaces it atomically.
    """
    dirpath = os.path.dirname(path) or "."
    tmp_path = ""
    try:
        orig_mode = os.stat(path).st_mode & 0o7777
        fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix=".gomplate-", suffix=".tmp")
        os.close(fd)
        result = subprocess.run(
            ["gomplate", "--missing-key", "error", "-o", tmp_path, "-f", path],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            os.chmod(tmp_path, orig_mode)
            os.replace(tmp_path, path)
            return True, ""
        return False, parse_gomplate_error(result.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def find_remaining(render_paths: list[str]) -> list[str]:
    """Return files that still contain markers after the render pass."""
    return [path for path, _ in find_templates(render_paths)]


def main(argv: list[str]) -> int:
    render_paths = argv[1:]
    if not render_paths:
        print("render_templates: no render paths given", file=sys.stderr)
        return 2

    templates = find_templates(render_paths)
    total = len(templates)
    replacements = sum(count for _, count in templates)

    failures: list[tuple[str, str]] = []
    for path, _count in templates:
        ok, err = render_file(path)
        if not ok:
            failures.append((path, err))

    remaining = find_remaining(render_paths)

    print(f"Rendered {total - len(failures)}/{total} files ({replacements} replacements)")
    if remaining:
        print(f"Raw templates remaining ({len(remaining)}):")
        for path in remaining:
            print(f"  {path}")
    if failures:
        print(f"Failed ({len(failures)}):")
        for path, err in failures:
            print(f"  {path}: {err}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
