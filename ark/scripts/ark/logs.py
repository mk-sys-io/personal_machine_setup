from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ark import ARK_DATA_DIR, LOG_COMMANDS


def cmd_logs(args: argparse.Namespace) -> None:
    """List per-command logs, or print the latest run of one command.

    Each per-command log is opened with mode="w", so the file already holds
    only the latest run. Read-only: no opslog session markers (configuring
    opslog here would truncate the very log being read).
    """
    logs_dir = Path(f"{ARK_DATA_DIR}/logs")
    if not args.log_cmd:
        found = False
        for name in LOG_COMMANDS:
            path = logs_dir / f"{name}.log"
            if not path.is_file():
                continue
            found = True
            stat = path.stat()
            mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(stat.st_mtime))
            print(f"{name:<8} {path}  ({stat.st_size} bytes, {mtime})")
        if not found:
            print("No ark command logs yet — run: ark enable / ark disable "
                  "/ ark abort")
        return

    path = logs_dir / f"{args.log_cmd}.log"
    if not path.is_file():
        sys.exit(f"Error: no {args.log_cmd}.log yet — run: ark {args.log_cmd}")
    try:
        sys.stdout.write(path.read_text())
    except OSError as e:
        sys.exit(f"Error: cannot read {path}: {e}")
