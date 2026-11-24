#!/usr/bin/env python3
"""Skeleton. Copy, rename, delete unused parts. Cite --help, not this docstring."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def confirm(question: str) -> bool:
    """Prompt only at a TTY. Enter aborts. Accepts y and yes."""
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def require_consent(args: argparse.Namespace, question: str) -> int | None:
    """None means proceed; an exit code means stop."""
    if args.yes:
        return None
    if not sys.stdin.isatty():
        print("error: no TTY; re-run with --yes", file=sys.stderr)
        return EXIT_USAGE
    if confirm(question):
        return None
    print("aborted", file=sys.stderr)
    return EXIT_FAIL


def emit_json(payload: object) -> None:
    """The only stdout write on a --json path."""
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-y", "--yes", action="store_true", help="skip confirmation")

    ap = argparse.ArgumentParser(prog="tool", description="one line")
    ap.add_argument("--version", action="version", version="tool 0.1.0")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", parents=[common], help="read state")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("apply", parents=[common], help="change state (mutating)")
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan, write nothing"
    )
    p.set_defaults(func=cmd_apply)
    return ap


def cmd_show(args: argparse.Namespace) -> int:
    data = {"ok": True}
    if args.json:
        emit_json(data)
    else:
        print("ok")
    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    if args.dry_run:
        print("would apply")
        return EXIT_OK
    stop = require_consent(args, "Apply?")
    if stop is not None:
        return stop
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
