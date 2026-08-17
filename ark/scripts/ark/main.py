from __future__ import annotations

import argparse
import sys

import opslog
from cask import lib
from cask.lib import CaskError

from ark import ARK_DATA_DIR, LOG_COMMANDS
from ark.abort import run as cmd_abort_run
from ark.disable import cmd_disable
from ark.enable import cmd_enable
from ark.logs import cmd_logs
from ark.status import cmd_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Ark — internet lockdown CLI")
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("enable", help="Enable focused mode")
    subs.add_parser("disable", help="Disable focused mode")
    subs.add_parser(
        "abort", help="Roll back an incomplete enable (snapshot restore)"
    )
    logs_parser = subs.add_parser("logs", help="Show command logs")
    logs_parser.add_argument(
        "log_cmd",
        nargs="?",
        choices=LOG_COMMANDS,
        help="Command whose latest run to print (default: list all)",
    )
    status_parser = subs.add_parser(
        "status", help="Live system status report (mode, sudo, network, namespace, timelock)"
    )
    status_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Per-check detail (rule counts, realpaths, raw metadata)",
    )
    status_flags = status_parser.add_mutually_exclusive_group()
    status_flags.add_argument("--plain", action="store_true", help="No ANSI color")
    status_flags.add_argument("--json", action="store_true", help="Structured JSON output")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # TTY gate (after parse so --help works headless): interactive commands
    # need a terminal; `logs` and `status` are read-only and are the headless
    # post-mortem / status tools, so they are exempt.
    if args.command not in ("logs", "status") and not sys.stdin.isatty():
        sys.exit("Error: ark requires an interactive terminal")

    # abort, logs, and status bypass the generic opslog session below: abort
    # configures abort.log itself, logs is read-only (configuring opslog would
    # truncate the very log it reads — mode="w"), and status never writes a
    # log (read-only report; a session here would create a stray status.log).
    if args.command == "abort":
        sys.exit(cmd_abort_run(args))
    if args.command == "logs":
        cmd_logs(args)
        sys.exit(0)
    if args.command == "status":
        sys.exit(cmd_status(args))

    opslog.configure("ark", file=f"{ARK_DATA_DIR}/logs/{args.command}.log",
                     mode="w")
    # Label the cask SIGTERM/failure session path with the real command —
    # cask.lib defaults to "cask", which would write a mismatched
    # `END cask: FAILED` into disable.log (prompt_duration cancels,
    # import-time signal handler).
    lib.set_component(args.command)
    opslog.session(args.command)
    try:
        {"enable": cmd_enable, "disable": cmd_disable}[args.command]()
    except KeyboardInterrupt:
        print("\nCancelled.")
        opslog.end_session(args.command, "FAILED")
        sys.exit(0)
    except CaskError as e:
        opslog.end_session(args.command, "FAILED")
        sys.exit(f"Error: {e}")
    # No subcommand may return — every path exits internally. Never write a
    # fabricated OK session; fail loud so a refactor that lets a command
    # return is caught instead of silently closing "OK".
    opslog.end_session(args.command, "FAILED")
    sys.exit(f"Error: {args.command} returned without exiting — check the log")
