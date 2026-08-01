#!/usr/bin/python3
"""Shared Python logging module for linux_setup tools.

A thin, opinionated configuration layer over the stdlib ``logging`` module
(Logger / Handler / Filter / Formatter), not a reimplementation — the Python
mirror of ``lib/common.sh`` logging for every Python tool in the repo.

Zero deps. Call ``configure()`` once at the tool's entry point, never at
import; helpers no-op until then.

    import opslog
    opslog.configure("ark", file="/opt/ark/logs/enable.log", mode="w")
    opslog.session("enable")          # exact line: === START enable ===
    opslog.set_step("Preflight")      # STEP line + (step) file annotation
    opslog.ok("milestone passed")     # ok / warn / error / info / debug
    opslog.end_session("enable", "OK")# exact line: === END enable: OK ===

Levels: RAW=0 (verbatim markers — always shown), DEBUG/INFO (file always,
terminal verbose-only), OK=21 (default terminal floor), STEP=25, WARNING,
ERROR. Thresholds are filter-based because RAW sits below DEBUG. File = full
record (flush per entry); terminal = curated stderr view, ANSI only when a
TTY. Never log secret values — register_secret()/redact() drive a sink
scrubber. Full guide: lib/python/README.md.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import threading
import time
import traceback

# ── Custom levels ─────────────────────────────────────────────────────────────
# OK=21 and STEP=25 are the only custom levels (stdlib permits levels between
# existing ones). RAW=0 exists only for verbatim session markers (abort gate).

RAW = 0
OK = 21
STEP = 25

logging.addLevelName(RAW, "RAW")
logging.addLevelName(OK, "OK")
logging.addLevelName(STEP, "STEP")

_CUSTOM_LEVELS = {"RAW": RAW, "OK": OK, "STEP": STEP}

_STDLIB_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}

# ── Module state ──────────────────────────────────────────────────────────────

_logger: logging.Logger | None = None
_component = ""
_secrets: set[str] = set()
_current_step: str | None = None
_lock = threading.RLock()

# ── Silent placeholder logger ─────────────────────────────────────────────────
# Helpers no-op until configure() runs; get_logger() never self-configures.

_PLACEHOLDER = logging.getLogger("linuxsetup.silent")
_PLACEHOLDER.setLevel(100)
_PLACEHOLDER.propagate = False
_PLACEHOLDER.addHandler(logging.NullHandler())


# ── Threshold filters (filter-based, not handler-level) ──────────────────────
# RAW(0) sits below DEBUG(10), so the stdlib handler-level check
# (record.levelno >= hdlr.level) would silently drop the session markers.
# Both handlers stay at level 0 (NOTSET) and floors are enforced here.

class _TerminalFilter(logging.Filter):
    """Admit RAW always plus anything at/above the terminal floor."""

    def __init__(self, terminal_level: int) -> None:
        super().__init__()
        self._floor = terminal_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == RAW or record.levelno >= self._floor


class _FileFilter(logging.Filter):
    """Admit RAW always plus anything at/above the file floor — the file is
    the complete record, abort-gate markers included."""

    def __init__(self, file_level: int) -> None:
        super().__init__()
        self._floor = file_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == RAW or record.levelno >= self._floor


# ── Sink scrubber ─────────────────────────────────────────────────────────────
# Redacts any registered secret from every record before it is written, so a
# missed call site or an f-string cannot leak a value that was ever registered.

class _SecretScrubber(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _secrets:
            return True
        msg = record.getMessage()
        for secret in _secrets:
            if secret in msg:
                record.msg = msg.replace(secret, "<redacted>")
                record.args = ()
                break
        if record.exc_info:
            exc_text = record.exc_text or "".join(
                traceback.format_exception(*record.exc_info)
            )
            for secret in _secrets:
                exc_text = exc_text.replace(secret, "<redacted>")
            record.exc_text = exc_text
            record.exc_info = None
        return True


# ── Flush-per-record file handler ─────────────────────────────────────────────

class _FlushFileHandler(logging.FileHandler):
    """FileHandler flushed per record — a SIGKILL never loses the tail."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


# ── File-only step/component annotation ───────────────────────────────────────
# The (current step) lives in the file (full detail) only; the terminal gets it
# via the STEP line itself. A filter injects the fields so the formatter stays
# static.

class _StepFilter(logging.Filter):
    """Injects component + (current step) onto records for the file handler."""

    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        record.component = _component
        record.step = f" ({_current_step})" if _current_step else ""
        return True


class _FileFormatter(logging.Formatter):
    """UTC full-detail file format; RAW records render message-only (the
    verbatim abort-gate markers must survive formatter changes byte-for-byte)."""

    converter = time.gmtime

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s UTC %(levelname)-5s %(component)s%(step)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno == RAW:
            return record.getMessage()
        return super().format(record)


# ── Terminal formatter ─────────────────────────────────────────────────────────
# Compact labels (>>> STEP,  OK/WARN/ERROR,  · sub-detail), no timestamp or
# component — the terminal is the curated view, the file is the detail. Color
# only when the stream is a TTY; the flag is captured once at configure().

_ANSI_RESET = "\x1b[0m"
_ANSI_BOLD = "\x1b[1m"
_ANSI_DIM = "\x1b[2m"
_ANSI_GREEN = "\x1b[32m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_RED = "\x1b[31m"

_TERM_COLORS: dict[int, str] = {
    STEP: _ANSI_BOLD,
    OK: _ANSI_GREEN,
    logging.WARNING: _ANSI_YELLOW,
    logging.ERROR: _ANSI_RED,
    logging.INFO: _ANSI_DIM,
    logging.DEBUG: _ANSI_DIM,
}

_TERM_LABELS: dict[int, str] = {
    OK: "OK",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
}


class _TerminalFormatter(logging.Formatter):
    """Compact terminal format; ANSI only when the stream is a TTY."""

    def __init__(self, color: bool) -> None:
        super().__init__()
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno == RAW:
            return record.getMessage()
        if record.levelno == STEP:
            line = f">>> {record.getMessage()}"
        elif record.levelno in (logging.DEBUG, logging.INFO):
            line = f"  {'·':<5} {record.getMessage()}"
        else:
            label = _TERM_LABELS.get(record.levelno, record.levelname)
            line = f"  {label:<5} {record.getMessage()}"
        if self._color:
            code = _TERM_COLORS.get(record.levelno)
            if code is not None:
                line = f"{code}{line}{_ANSI_RESET}"
        if record.exc_text:
            line += f"\n{record.exc_text}"
        elif record.exc_info:
            line += f"\n{self.formatException(record.exc_info)}"
        return line


# ── Log dir setup ──────────────────────────────────────────────────────────────
# Callers own dir policy: base_dir (e.g. $ARK_DATA_PATH) must already exist —
# a missing base means the install is broken, so fail fast rather than silently
# create a wrong-owner tree. The log dir itself (parent of `file`) is created on
# demand; hardened to root 0750 only when running as root (ark is sudo-launched).

def _ensure_log_dir(file: str, base_dir: str | None) -> None:
    parent = os.path.dirname(os.path.abspath(file))
    if base_dir is not None and not os.path.isdir(base_dir):
        raise FileNotFoundError(f"base dir {base_dir} missing — install broken")
    if os.path.isdir(parent):
        return
    os.makedirs(parent, exist_ok=True)
    if os.geteuid() == 0:
        os.chmod(parent, 0o750)
        os.chown(parent, 0, 0)


# ── Public API ────────────────────────────────────────────────────────────────

def _normalize_level(level: int | str) -> int:
    """Resolve a level given as int or registered name (e.g. "OK", "WARNING")."""
    if isinstance(level, int):
        return level
    if level in _CUSTOM_LEVELS:
        return _CUSTOM_LEVELS[level]
    if level in _STDLIB_LEVELS:
        return _STDLIB_LEVELS[level]
    raise TypeError(f"unknown log level: {level!r}")


def configure(
    component: str = "",
    file: str | None = None,
    mode: str = "w",
    echo: bool = True,
    terminal_level: int | str = OK,
    file_level: int | str = logging.DEBUG,
    logger: str = "linuxsetup",
    base_dir: str | None = None,
) -> logging.Logger:
    """Idempotent setup; returns the named Logger. Call once at the tool's
    entry point, never at import (helpers no-op until then).

    component: label shown in file lines (e.g. "ark").
    file: log path; None → terminal only. mode="w" truncates per run and
        re-chmod/chowns to root 0640 at open; mode="a" appends (persistent
        tool logs).
    echo: False → file only. Default: both sinks.
    terminal_level/file_level: int or level name ("OK", "WARNING", ...) — the
        floors are filter-enforced (handlers sit at NOTSET), so RAW markers
        are always admitted. Defaults: terminal OK, file DEBUG.
    logger: named logger with propagate=False — a tool that also configures
        the root logger never double-prints (the basicConfig footgun).
    base_dir (e.g. $ARK_DATA_PATH): must already exist — raises
        FileNotFoundError if missing (install broken → fail fast). The log
        dir itself (parent of `file`) is auto-created; hardened to root
        0750/chown root when running as root.
    """
    global _logger, _component

    terminal_level = _normalize_level(terminal_level)
    file_level = _normalize_level(file_level)

    with _lock:
        lg = logging.getLogger(logger)
        lg.setLevel(1)
        lg.propagate = False
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()

        if echo:
            term = logging.StreamHandler(sys.stderr)
            term.setLevel(logging.NOTSET)
            term.setFormatter(_TerminalFormatter(sys.stderr.isatty()))
            term.addFilter(_TerminalFilter(terminal_level))
            term.addFilter(_SecretScrubber())
            lg.addHandler(term)

        if file is not None:
            _ensure_log_dir(file, base_dir)
            fh = _FlushFileHandler(file, mode=mode)
            fh.setLevel(logging.NOTSET)
            fh.setFormatter(_FileFormatter())
            fh.addFilter(_FileFilter(file_level))
            fh.addFilter(_StepFilter())
            fh.addFilter(_SecretScrubber())
            lg.addHandler(fh)
            if mode == "w" and os.geteuid() == 0:
                os.chmod(file, 0o640)
                os.chown(file, 0, 0)

        _logger = lg
        _component = component
        return lg


def get_logger() -> logging.Logger:
    """The configured logger; a silent placeholder until configure() runs."""
    return _logger if _logger is not None else _PLACEHOLDER


def debug(msg: object, *args: object) -> None:
    get_logger().debug(msg, *args)


def info(msg: object, *args: object) -> None:
    get_logger().info(msg, *args)


def ok(msg: object, *args: object) -> None:
    get_logger().log(OK, msg, *args)


def warn(msg: object, *args: object) -> None:
    get_logger().warning(msg, *args)


def error(msg: object, *args: object) -> None:
    get_logger().error(msg, *args)


def exception(msg: object, *args: object) -> None:
    """Log an ERROR with the current exception's full trace (exc_info)."""
    get_logger().exception(msg, *args)


def raw(line: str) -> None:
    """Emit a verbatim, message-only line (RAW level, no prefix)."""
    lg = get_logger()
    record = lg.makeRecord(lg.name, RAW, "opslog", 0, line, (), None, None)
    lg.handle(record)


def set_step(name: str) -> None:
    """Log a STEP line and store the current step for the file annotation."""
    global _current_step
    _current_step = name
    get_logger().log(STEP, name)


def current_step() -> str | None:
    """The current step (None until set_step()); used by formatters."""
    return _current_step


def session(label: str) -> None:
    """Write the exact abort-gate opening marker: === START <label> ===."""
    raw(f"=== START {label} ===")


def end_session(label: str, status: str) -> None:
    """Write the exact abort-gate closing marker and clear the step."""
    global _current_step
    _current_step = None
    raw(f"=== END {label}: {status} ===")


def register_secret(value: str) -> None:
    """Register a secret for sink-level scrubbing. Values under 8 chars are
    skipped — substring scrubbing a short value would over-redact prose (the
    only failure mode is over-redaction, which is the safe direction)."""
    if len(value) >= 8:
        _secrets.add(value)


def redact(value: str) -> str:
    """Redact a value for safe interpolation; also registers it so the sink
    scrubber catches it anywhere it later appears."""
    register_secret(value)
    return "<redacted>"


# ── Optional CLI convenience (developer-facing tools only) ────────────────────
# Production tools (ark) never expose these — they show steps/milestones only
# and reveal detail through `ark logs <cmd>`. Install.py-style tools opt in.

def add_cli_args(parser: argparse.ArgumentParser) -> None:
    """Add --verbose/--quiet/--log-file to a tool's argument parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--verbose",
        action="store_true",
        help="Show INFO/DEBUG sub-detail on the terminal",
    )
    group.add_argument(
        "--quiet",
        action="store_true",
        help="Only errors on the terminal",
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help="Write the full-detail log to PATH",
    )


def configure_from_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    component: str,
    default_file: str | None = None,
) -> logging.Logger:
    """Configure from add_cli_args() flags (verbose→DEBUG, quiet→ERROR, else
    OK; file = --log-file or default_file). Returns the Logger."""
    if args.quiet:
        terminal_level = logging.ERROR
    elif args.verbose:
        terminal_level = logging.DEBUG
    else:
        terminal_level = OK
    return configure(
        component=component,
        file=args.log_file or default_file,
        terminal_level=terminal_level,
    )


# ── Self-test ─────────────────────────────────────────────────────────────────
# python3 lib/python/opslog.py --self-test runs a demo session (file + terminal,
# DEBUG/INFO file-only by default); re-run with --verbose to see them live.

def _self_test(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    logfile = os.path.join(tempfile.gettempdir(), "opslog-selftest.log")
    configure_from_args(parser, args, "selftest", default_file=logfile)
    session("selftest")
    set_step("demo")
    debug("debug line — file only (terminal only with --verbose)")
    info("info line — file only (terminal only with --verbose)")
    ok("milestone passed")
    warn("non-fatal warning")
    error("fatal error; session ends FAILED")
    end_session("selftest", "OK")
    sys.stderr.write(f"Full log: {logfile}\n")
    sys.stderr.write("Re-run with --verbose to see INFO/DEBUG on the terminal.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="opslog shared logging module — self-test"
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Run a demo session"
    )
    add_cli_args(parser)
    args = parser.parse_args()
    if not args.self_test:
        parser.print_help()
        sys.exit(1)
    _self_test(parser, args)
