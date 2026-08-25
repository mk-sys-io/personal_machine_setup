"""CLI: usage text, argument parsing, command dispatch, top-level error handling."""
from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import cast

from .auth import cmd_check, do_clean, do_reset, maybe_discover, prompt_providers
from .config import CURATED_DIR
from .errors import HelpRequest, ToolError, UsageError
from .probe import cmd_fetch_free, cmd_probe
from .providers import (
    FETCH_PROVIDERS,
    PROBE_PROVIDERS,
    PROVIDER_ORDER,
    PROVIDERS,
    ProviderId,
)


def usage() -> None:
    print(
        """pi-setup — Pi provisioning: credentials + model allowlist discovery

Usage:
  pi-setup auth [--provider <id>...] [--force|--reset [--yes]|check]
  pi-setup probe <provider>...|--all [--write]
  pi-setup fetch <provider>...|--all [--write]
  pi-setup clean [--yes]
  pi-setup dir

Commands:
  auth                prompt for missing providers (additive); after setup
                      offers to probe/fetch the configured providers
  auth --force        re-prompt even if already configured
  auth --reset [--yes]  back up + wipe ALL credentials, re-prompt;
                        with --provider <id>...: remove + re-prompt only those
                        (--provider is an 'auth'-only flag)
  auth check          run 'pi auth check --provider <id>' per target
                      (flag rules: 'check' rejects --reset; --reset rejects
                      --force; --yes requires --reset)
  probe <provider>...|--all [--write]  live chat-probe (opencode, nim,
                      gemini) — keeps models that answer a real Pi-like
                      request; --write saves curated files. Multiple
                      providers run in sequence and continue past
                      individual failures (exit 1 if any failed).
  fetch <provider>...|--all [--write]  fetch free-model lists (openrouter,
                      nararouter) — one GET each, no live verification;
                      --write saves curated files
  clean [--yes]       back up + wipe credentials AND cached model catalogs
                      (auth.json + models-store.json -> .bak); no re-prompting;
                      scripted complement to Pi's interactive /logout — follow
                      with 'pi-setup auth'
  dir                 list curated files in the live curated dir

Providers (fetch-based first — fast list downloads before slow live probes):
  openrouter   OpenRouter free models (list-fetched via `fetch`)
  nararouter   NaraRouter free models (list-fetched via `fetch`)
  opencode     OpenCode Zen (live free-tier probe, 45 s timeout)
  nim          NVIDIA NIM (live probe)
  gemini       Google AI Studio (live probe, 35 s timeout, 3-layer filter)

Env (dev/test): PI_AUTH_JSON, PI_CURATED_DIR, PI_STORE_JSON"""
    )


# Words that terminate a --provider id list (never valid provider ids)
COMMAND_WORDS = frozenset(
    {"auth", "dir", "check", "probe", "fetch", "add", "rm", "list", "clean"}
)


def validate_ids(ids: Sequence[str]) -> list[ProviderId]:
    out: list[ProviderId] = []
    last: str | None = None
    for id_ in ids:
        if id_ not in PROVIDERS:
            raise UsageError(
                f"unknown provider '{id_}' (expected: {' '.join(PROVIDER_ORDER)})"
            )
        if id_ != last:
            out.append(cast(ProviderId, id_))
            last = id_
    return out


def run_auth(argv: Sequence[str], selected: Sequence[ProviderId]) -> int:
    force = False
    reset = False
    yes = False
    mode = "add"
    for arg in argv:
        if arg == "--force":
            force = True
        elif arg == "--reset":
            reset = True
        elif arg == "--yes":
            yes = True
        elif arg == "check":
            mode = "check"
        elif arg in ("-h", "--help"):
            usage()
            return 0
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")
    # Flag-compatibility rules (fail fast, clap-style — never silently ignore):
    # 'check' is read-only and must never be shadowed by the destructive
    # --reset branch; --reset always re-prompts (hardcoded force=True), so
    # --force is meaningless next to it; --yes only guards a wipe.
    if reset and mode == "check":
        raise UsageError("'check' cannot be combined with --reset")
    if reset and force:
        raise UsageError("--force cannot be used with --reset (--reset always re-prompts)")
    if yes and not reset:
        raise UsageError("--yes requires --reset")
    targets: Sequence[ProviderId] = selected or PROVIDER_ORDER
    if reset:
        do_reset(targets, yes=yes)
        maybe_discover(targets)
    elif mode == "check":
        return cmd_check(targets)
    else:
        prompt_providers(targets, force=force)
        maybe_discover(targets)
    return 0


def parse_targets(
    argv: Sequence[str],
    *,
    verb: str,
    allowed: tuple[ProviderId, ...],
) -> tuple[list[ProviderId], bool]:
    """Shared probe/fetch parsing: positional provider ids or --all.

    Returns (targets, write). Ids are deduped preserving first occurrence;
    --all selects every allowed provider; mixing --all with explicit ids is
    rejected. Raises HelpRequest after printing usage for -h/--help.
    """
    chosen: list[ProviderId] = []
    all_flag = False
    write = False
    for arg in argv:
        if arg == "--write":
            write = True
        elif arg == "--all":
            all_flag = True
        elif arg in ("-h", "--help"):
            usage()
            raise HelpRequest
        elif arg in PROVIDERS:
            if cast(ProviderId, arg) not in allowed:
                other = "fetch" if verb == "probe" else "probe"
                why = (
                    "list-based (no live probing)"
                    if verb == "probe"
                    else "live-probed"
                )
                raise UsageError(f"'{arg}' is {why} — use: pi-setup {other} {arg}")
            if arg not in chosen:
                chosen.append(cast(ProviderId, arg))
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")
    if all_flag:
        if chosen:
            raise UsageError("--all cannot be combined with explicit provider ids")
        return list(allowed), write
    if not chosen:
        raise UsageError(f"{verb} needs a provider ({' '.join(allowed)}) or --all")
    return chosen, write


def run_discovery(verb: str, argv: Sequence[str]) -> int:
    """Run probe/fetch over one or more targets; continue past per-provider
    failures and exit 1 if any failed."""
    allowed = PROBE_PROVIDERS if verb == "probe" else FETCH_PROVIDERS
    try:
        targets, write = parse_targets(argv, verb=verb, allowed=allowed)
    except HelpRequest:
        return 0
    failed: list[ProviderId] = []
    multi = len(targets) > 1
    for i, prov in enumerate(targets):
        if multi:
            if i:
                print()
            print(f"== {prov} ==")
        try:
            if verb == "probe":
                cmd_probe(prov, write=write)
            else:
                cmd_fetch_free(prov, write=write)
        except ToolError as e:
            print(f"pi: {prov}: {e}", file=sys.stderr)
            failed.append(prov)
    if multi:
        ok = len(targets) - len(failed)
        note = f" ({', '.join(failed)})" if failed else ""
        print(f"done: {ok} ok, {len(failed)} failed{note}")
    return 1 if failed else 0


def run_probe(argv: Sequence[str]) -> int:
    return run_discovery("probe", argv)


def run_fetch(argv: Sequence[str]) -> int:
    return run_discovery("fetch", argv)


def cmd_dir() -> None:
    print(CURATED_DIR)
    if not CURATED_DIR.exists():
        print("  (directory does not exist yet)")
        return
    files = sorted(p.name for p in CURATED_DIR.iterdir() if p.suffix == ".json")
    if not files:
        print("  (empty)")
        return
    for f in files:
        print(f"  {f}")


def run_dir(argv: Sequence[str]) -> int:
    for arg in argv:
        if arg in ("-h", "--help"):
            usage()
            return 0
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")
    cmd_dir()
    return 0


def run_clean(argv: Sequence[str]) -> int:
    yes = False
    for arg in argv:
        if arg == "--yes":
            yes = True
        elif arg in ("-h", "--help"):
            usage()
            return 0
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")
    do_clean(yes=yes)
    return 0


def run(argv: Sequence[str]) -> int:
    selected: list[ProviderId] = []
    positionals: list[str] = []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--provider":
            ids: list[str] = []
            while args and not args[0].startswith("-") and args[0] not in COMMAND_WORDS:
                ids.append(args.pop(0))
            if not ids:
                raise UsageError("--provider needs at least one id")
            selected.extend(validate_ids(ids))
        elif arg.startswith("--provider="):
            selected.extend(validate_ids([arg.split("=", 1)[1]]))
        elif arg in ("-h", "--help"):
            usage()
            return 0
        else:
            positionals.append(arg)
    selected = list(dict.fromkeys(selected))
    if not positionals:
        usage()
        return 2
    group = positionals[0]
    rest = positionals[1:]
    if group != "auth" and selected:
        hint = (
            f" — pass providers as arguments: pi-setup {group} <provider>..."
            if group in ("probe", "fetch")
            else ""
        )
        raise UsageError(f"--provider applies to 'auth'{hint}")
    if group == "auth":
        return run_auth(rest, selected)
    if group == "probe":
        return run_probe(rest)
    if group == "fetch":
        return run_fetch(rest)
    if group == "dir":
        return run_dir(rest)
    if group == "clean":
        return run_clean(rest)
    raise UsageError(f"unknown command '{group}' (see --help)")


def main(argv: Sequence[str]) -> int:
    try:
        return run(argv)
    except ToolError as e:
        print(f"pi: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("pi: aborted", file=sys.stderr)
        return 1
