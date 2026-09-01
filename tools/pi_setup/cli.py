"""CLI: usage text, argument parsing, command dispatch, top-level error handling."""
from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import cast

from .auth import clear, cmd_check, copy_vault_to_pi, generate_manifest, maybe_discover
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
  pi-setup auth [--provider <id>...|--all] [check]
  pi-setup probe <provider>...|--all [--write]
  pi-setup fetch <provider>...|--all [--write]
  pi-setup clear
  pi-setup dir

Commands:
  auth                copy vault → Pi auth.json, show status, choose
                      provider(s) to probe/fetch, generate manifest
  auth check          run 'pi auth check --provider <id>' per target
  auth --all          non-interactive: copy + probe/fetch all providers
  auth --provider <id>...  explicit provider selection
  probe <provider>...|--all [--write]  live chat-probe (opencode, nim,
                      gemini) — keeps models that answer a real Pi-like
                      request; --write saves curated files. Multiple
                      providers run in sequence and continue past
                      individual failures (exit 1 if any failed).
  fetch <provider>...|--all [--write]  fetch free-model lists (openrouter,
                      nararouter) — one GET each, no live verification;
                      --write saves curated files
  clear               one-pass wipe of Pi auth.json + models-store.json
                      (vault untouched — re-run 'pi-setup auth' to re-copy)
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
    {"auth", "dir", "check", "probe", "fetch", "add", "rm", "list", "clear"}
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
    mode = "add"
    all_flag = False
    for arg in argv:
        if arg == "--all":
            all_flag = True
        elif arg == "check":
            mode = "check"
        elif arg in ("-h", "--help"):
            usage()
            return 0
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")

    if mode == "check":
        targets: Sequence[ProviderId] = selected or PROVIDER_ORDER
        return cmd_check(targets)

    if all_flag and selected:
        raise UsageError("--all cannot be combined with explicit provider ids")

    if all_flag:
        targets = list(PROVIDER_ORDER)
    elif selected:
        targets = list(selected)
    else:
        # Interactive selection: show curated list with status
        from provider_registry.config import GOPASS_ENTRY_PREFIX, gopass_show_field
        print("Available providers:")
        for pid in PROVIDER_ORDER:
            p = PROVIDERS[pid]
            has_cred = (
                gopass_show_field(f"{GOPASS_ENTRY_PREFIX}/{pid}", "key") is not None
            )
            status = " [configured]" if has_cred else ""
            print(f"  {pid:12s}  {p.label}{status}")
        print()
        try:
            raw = input("Provider(s) to configure (space-separated, or Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            raw = ""
        if not raw:
            print("pi: skipped (no provider selected)")
            return 0
        targets = validate_ids(raw.split())

    copy_vault_to_pi(targets)
    maybe_discover(targets)
    generate_manifest()
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


def run_clear(argv: Sequence[str]) -> int:
    for arg in argv:
        if arg in ("-h", "--help"):
            usage()
            return 0
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")
    clear()
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
    if group == "clear":
        return run_clear(rest)
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
