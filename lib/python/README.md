# opslog — shared Python logging module

One importable logging module for every Python tool in this repo (ark, the vault
rework, netmgr, the future `install.py`): a thin, opinionated layer over stdlib
`logging` — the Python mirror of `lib/common.sh` logging. Stdlib only, zero deps.
Source: `lib/python/opslog.py`; deployed to `/opt/ark/scripts/opslog.py`.

## Quick reference
- [Why](#why) · [Quick start](#quick-start) · [Levels](#levels)
- [Destinations & verbosity](#destinations--verbosity) · [Session markers](#session-markers)
- [Secrets](#secrets) · [Deploy](#deploy) · [Consumers](#consumers)
- [Conventions](#conventions) · [Deliberately omitted](#deliberately-omitted)

## Why
stdlib `logging` costs nothing and every guide builds on it, never replaces it. A
hand-rolled console logger (like the old `seal_lib`) can't redirect to a file,
isn't thread-safe, and breaks when reused as a module. This module only packages
the per-tool opinion — format, levels, destinations, step state, session markers —
that would otherwise be copy-pasted across tools.

## Quick start
```python
import opslog
opslog.configure("ark", file="/opt/ark/logs/enable.log", mode="w")
opslog.session("enable")
opslog.set_step("Preflight")
opslog.ok("lockdown dir present")
opslog.end_session("enable", "OK")
```
Call `configure()` once at the entry point, never at import; helpers no-op until
then. Smoke test: `python3 lib/python/opslog.py --self-test` (re-run with
`--verbose`). Full API reference: the docstrings (`help(opslog)`).

## Levels
| Level | Value | Terminal default | Meaning |
|---|---|---|---|
| RAW | 0 | always | verbatim line, no prefix — machine-readable markers |
| DEBUG | 10 | hidden | file only |
| INFO | 20 | `--verbose` only | sub-step detail |
| OK | 21 | ✓ | milestone passed — default floor |
| STEP | 25 | ✓ | phase begin |
| WARN | 30 | ✓ | non-fatal |
| ERROR | 40 | ✓ | fatal — session ends `FAILED` |

`OK=21` and `STEP=25` are the only custom levels, preserving the established
`[STEP]` → `[OK]` vocabulary. Thresholds are **filter-based, not handler-level**:
RAW(0) sits below DEBUG(10),
so the stdlib handler-level check (`record.levelno >= hdlr.level`) would drop
the verbatim markers before custom code runs. Both handlers sit at level 0;
filters admit RAW always plus `levelno >= floor`.

## Destinations & verbosity
- **File** (`file=`): full record, flush per entry (a SIGKILL never loses the
  tail). `mode="w"` truncates per run and re-chmods root 0640. Line format:
  `2026-08-01 16:00:00 UTC OK    ark (Preflight) msg`.
- **Terminal**: stderr, curated view (STEP `>>>`, `  OK/WARN/ERROR`, `  ·` for
  INFO sub-detail), no timestamp/component, ANSI only when `isatty()`.
- Verbosity is **configuration, not a flag**: the file always captures full
  detail; the terminal shows what the tool chooses. `--verbose`/`--quiet` via
  `add_cli_args()` are an optional convenience for developer-facing tools only —
  ark never exposes them; it reveals detail via `ark logs <cmd>`.

## Session markers
`session(label)` / `end_session(label, status)` emit exact, message-only lines at
RAW level — `=== START <label> ===` / `=== END <label>: <status> ===` — verbatim
in both sinks and immune to formatter changes. RAW exists precisely so
machine-readable markers survive byte-for-byte. (What a marker *means* is the
consumer's business — ark's abort gate keys off these lines; see the ark flow
docs for that contract.)

## Secrets
Never log secret **values** — the opslog is plaintext evidence excluded from
timeshift restores. Messages describe the operation, not the value. Every secret
is registered at its creation point (`register_secret(value)`, ≥8-char guard) so
a scrubber filter redacts it from every record on both sinks — even a missed call
site or an f-string can't leak a registered value. `redact(value)` registers and
returns `"<redacted>"` for interpolation.

## Deploy
Source: `lib/python/opslog.py`, copied by `lib/60-ark.sh` to
`/opt/ark/scripts/opslog.py` (root:root 644). ark/netmgr/vault — already on the
`/opt/ark/scripts` path — import it with no new wiring. Non-ark tools add
`$REPO_ROOT/lib/python` to `sys.path`, then `import opslog`. Keeping the source
in the repo (not only under `/opt/ark`) means bootstrap tools like the future
`install.py` can log **before** ark exists — no circular dependency.

## Consumers
- **ark** — `configure("ark", file=/opt/ark/logs/<cmd>.log, mode="w")` per
  command; session markers; no `--verbose` (detail via `ark logs`).
- **vault rework** — replaces the `seal_lib` `log()/init_log()/step()`;
  `emergency_exit()` wraps `error()` + `end_session(..., "FAILED")`.
- **netmgr** — `configure()` at CLI entry; modules emit via the opslog helpers so
  records route to whichever process imported it.
- **install.py (planned)** — future Python successor to today's `install.sh`
  (bash + `common.sh`); `configure("install", file=~/.config/install/install.<date>.log,
  mode="a")`; may opt into `add_cli_args()`.

## Conventions
- `info()`/`debug()` are the **only** way to emit per-item detail — never
  `print()` or ad-hoc per-call formatting — so the STEP/sub-step pattern stays
  uniform across tools.
- Helpers: `debug/info/ok/warn/error/exception(msg, *args)` take lazy
  `%`-style args; `exception()` logs an ERROR with the full trace (`exc_info`).
- Logging is thread-safe (stdlib); step state is a **module global**, not
  thread-local.
- Terminal output goes to **stderr**, keeping stdout clean for prompts and data
  output (`blocklist generate`, `timeshift --list`).

## Deliberately omitted
`dictConfig` (static vs per-tool variation); JSON/structlog/loguru (zero-dep
single-operator CLIs); rotation (`mode="w"` per-run contract; stdlib
`RotatingFileHandler` if a persistent tool outgrows one file); Rich/colorama
(terminal redraw + flush-per-entry contract; plain ANSI-gated codes only);
log-integrity checksums/HMAC (defeated by root on the host; 0640 perms already
restrict writers).
