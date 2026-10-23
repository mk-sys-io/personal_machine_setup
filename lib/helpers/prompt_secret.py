#!/usr/bin/env python3
"""Hidden-input prompt shared by bash and Python install steps.

One implementation for every interactive secret read (GitHub token, restic
repo password, B2 credentials). Import it in Python, or execute it by path
from bash; either way the terminal handling, paste scrub, character-count
confirmation, and timeout are identical.

Security contract:
  - input echo and IEXTEN are disabled (ICANON/ISIG stay on) for the read;
  - the value is written only to stdout, which callers MUST capture;
  - the CLI refuses to run when stdout is a terminal, so a manual invocation
    cannot echo the secret back to the screen;
  - diagnostics on stderr and the tty never include the value.

CLI contract:
    prompt_secret.py --message MSG [--timeout SECS]
      stdout: sanitized secret, one line (may be empty)
      tty:    the message, then "accepted (N chars)"
      stderr: diagnostics, plus the count when stderr is not a tty (the
              install.py child pump case, so the install log records it)
      rc:     0 value read, 1 no usable terminal/EOF/setup failure,
              2 usage error, 3 timeout

Stdlib only; import has no side effects.
"""
from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import time

MAX_INPUT_BYTES = 8192


def _sanitize(raw: str) -> str:
    """Strip bracketed-paste markers and control bytes, then surrounding space.

    Terminals that honour bracketed paste wrap the payload in ESC[200~ /
    ESC[201~. With IEXTEN cleared, Ctrl+V arrives as a bare 0x16 instead of
    arming VLNEXT, so control bytes are dropped as well. This mirrors the
    restic prompt scrub exactly.
    """
    cleaned = raw.replace("\x1b[200~", "").replace("\x1b[201~", "")
    cleaned = "".join(c for c in cleaned if c >= " " and c != "\x7f")
    return cleaned.strip()


def _write(fd: int, text: str) -> None:
    data = text.encode("utf-8", "replace")
    while data:
        written = os.write(fd, data)
        if written <= 0:
            raise OSError("short write to /dev/tty")
        data = data[written:]


def _notice(fd: int, prog: str, msg: str) -> None:
    """Show a non-secret notice once on the terminal and in the log.

    When stderr is the terminal (standalone), stderr is the display. When
    stderr is captured (install.py's child pump), the operator sees /dev/tty
    and the log receives the stderr copy.
    """
    if sys.stderr.isatty():
        print(f"{prog}: {msg}", file=sys.stderr)
    else:
        _write(fd, f"{prog}: {msg}\n")
        print(f"{prog}: {msg}", file=sys.stderr)


def prompt_secret(
    message: str, timeout: float | None = None, prog: str = "prompt-secret"
) -> str | None:
    """Read one hidden line from /dev/tty.

    Returns the sanitized value (empty string allowed), or None when there is
    no usable terminal, input hits EOF, or the line is too long. Raises
    TimeoutError when `timeout` seconds elapse before Enter; KeyboardInterrupt
    propagates. `message` is the caller's full prompt text; `prog` prefixes
    diagnostics only.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError as exc:
        print(f"{prog}: no usable terminal: {exc}", file=sys.stderr)
        return None
    try:
        try:
            saved = termios.tcgetattr(fd)
        except termios.error as exc:
            print(f"{prog}: /dev/tty is not termios-capable: {exc}", file=sys.stderr)
            return None
        attrs = saved[:]
        attrs[3] &= ~(termios.ECHO | termios.IEXTEN)
        try:
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except termios.error as exc:
            print(f"{prog}: cannot disable terminal echo: {exc}", file=sys.stderr)
            return None

        try:
            base = message.rstrip().rstrip(":").rstrip()
            hint = "  (input hidden by design — security"
            if timeout:
                hint += f"; {timeout:g}s timeout"
            hint += "; Ctrl+Shift+V or middle-click pastes) → "
            _write(fd, f"{base}\n{hint}")
            deadline = time.monotonic() + timeout if timeout else None
            chunks = b""
            while b"\n" not in chunks and b"\r" not in chunks:
                wait = None if deadline is None else max(0.0, deadline - time.monotonic())
                ready, _, _ = select.select([fd], [], [], wait)
                if not ready:
                    raise TimeoutError(f"no input after {timeout:g}s")
                data = os.read(fd, MAX_INPUT_BYTES - len(chunks))
                if not data:
                    raise EOFError("no input (EOF)")
                chunks += data
                if len(chunks) >= MAX_INPUT_BYTES:
                    raise ValueError(f"input too long (> {MAX_INPUT_BYTES} bytes)")
        except TimeoutError as exc:
            try:
                _write(fd, "\n")
                termios.tcflush(fd, termios.TCIFLUSH)
            except (OSError, termios.error):
                pass
            _notice(fd, prog, str(exc))
            raise
        except (EOFError, ValueError) as exc:
            try:
                _write(fd, "\n")
                termios.tcflush(fd, termios.TCIFLUSH)
            except (OSError, termios.error):
                pass
            _notice(fd, prog, str(exc))
            return None
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, saved)
            except termios.error:
                pass

        secret = _sanitize(chunks.decode("utf-8", "replace"))
        _notice(fd, prog, f"accepted ({len(secret)} chars)")
        return secret
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer number of seconds") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read a secret from /dev/tty without echoing it."
    )
    parser.add_argument("--message", required=True, help="prompt text shown to the operator")
    parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        help="overall seconds allowed before Enter (default: wait forever)",
    )
    args = parser.parse_args(argv)
    if sys.stdout.isatty():
        print(
            "prompt-secret: refusing to write a secret to a terminal; capture stdout",
            file=sys.stderr,
        )
        return 1
    try:
        secret = prompt_secret(args.message, args.timeout)
    except TimeoutError:
        return 3
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"prompt-secret: prompt failed: {exc}", file=sys.stderr)
        return 1
    if secret is None:
        return 1
    try:
        sys.stdout.buffer.write(secret.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except OSError as exc:
        print(f"prompt-secret: cannot write the value: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
