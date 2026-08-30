"""CLI: list/get/add/remove/check against the shared vault.

The generic storage primitive — the single place that writes the vault
(vault.json) that pi-setup auth builds its UX on top of. No wipe command:
deleting one credential is 'remove <id>'; wiping Pi's deployed auth.json is
'pi-setup clear's job.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import cast

from .config import VAULT_JSON
from .credentials import JsonCredentialStore, get_credentials
from .errors import ToolError, UsageError
from .providers import (
    PROVIDER_ORDER,
    PROVIDERS,
    ProviderId,
    get_provider,
    list_providers,
)


def usage() -> None:
    print(
        """provider-registry — shared provider registry + credential vault

Usage:
  provider-registry list
  provider-registry get <provider>
  provider-registry add <provider>
  provider-registry remove <provider>
  provider-registry check <provider>

Commands:
  list                list providers + which have creds in vault.json
  get <provider>      print the stored key (vault only; errors if missing)
  add <provider>      prompt for API key + probe-or-fetch strategy, store both
  remove <provider>   delete the provider's vault entry
  check <provider>    resolve + report source (vault.json / missing)

Providers:
  openrouter   OpenRouter (free)          fetch
  nararouter   NaraRouter (free)          fetch
  opencode     OpenCode Zen               probe
  nim          NVIDIA NIM (live)          probe
  gemini       Google AI Studio (Gemini)  probe

Env (dev/test): PI_VAULT_JSON"""
    )


def _prompt_key(pid: ProviderId) -> str | None:
    p = get_provider(pid)
    print(f"provider: {pid} ({p.label})", file=sys.stderr)
    try:
        # SECURITY: input() echoes the key on screen — traded for paste support
        # (getpass's raw-mode input breaks Ctrl+V/middle-click on most terminals)
        val = input(
            "  API key (press Enter to skip; '!cmd' or '$ENV' stored verbatim): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None
    return val


def _prompt_strategy(pid: ProviderId) -> str | None:
    p = get_provider(pid)
    default = p.probe.strategy if p.probe is not None else None
    if default is None:
        return None
    prompt = f"  discovery strategy [{default}] (probe/fetch): "
    try:
        val = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None
    if not val:
        return default
    if val not in ("probe", "fetch"):
        raise UsageError(f"invalid strategy '{val}' (expected probe or fetch)")
    return val


def cmd_list() -> None:
    store = JsonCredentialStore()
    for p in list_providers():
        key = store.get(p.id)
        strat = store.strategy(p.id)
        status = "set" if key else "missing"
        strat_note = f"  strategy: {strat}" if strat else ""
        print(f"  {p.id:<12} {p.label:<28} {status}{strat_note}")


def cmd_add(pid: ProviderId) -> None:
    store = JsonCredentialStore()
    key = _prompt_key(pid)
    if key is None:
        print(f"provider-registry: skipped {pid} (input closed)")
        return
    if not key:
        print(f"provider-registry: skipped {pid} (no key entered)")
        return
    strategy = _prompt_strategy(pid)
    store.set(pid, key, strategy=strategy)
    print(f"provider-registry: wrote {pid} to {VAULT_JSON} (0600)")


def cmd_remove(pid: ProviderId) -> None:
    store = JsonCredentialStore()
    if store.get(pid) is None:
        print(f"provider-registry: {pid} has no vault entry — nothing to remove")
        return
    store.delete(pid)
    print(f"provider-registry: removed {pid} from {VAULT_JSON}")


def cmd_check(pid: ProviderId) -> int:
    store = JsonCredentialStore()
    key = store.get(pid)
    if key is None:
        print(f"{pid}: missing (no entry in {VAULT_JSON})")
        return 1
    strat = store.strategy(pid)
    note = f", strategy: {strat}" if strat else ""
    print(f"{pid}: set (vault.json{note})")
    return 0


def run(argv: Sequence[str]) -> int:
    if not argv:
        usage()
        return 2
    verb = argv[0]
    rest = list(argv[1:])
    if verb in ("-h", "--help"):
        usage()
        return 0
    if verb == "list":
        if rest and rest[0] in ("-h", "--help"):
            usage()
            return 0
        if rest:
            raise UsageError("list takes no arguments (see --help)")
        cmd_list()
        return 0
    if verb in ("get", "add", "remove", "check"):
        if rest and rest[0] in ("-h", "--help"):
            usage()
            return 0
        if len(rest) != 1:
            raise UsageError(f"{verb} needs exactly one provider id (see --help)")
        pid = rest[0]
        if pid not in PROVIDERS:
            raise UsageError(
                f"unknown provider '{pid}' (expected: {' '.join(PROVIDER_ORDER)})"
            )
        typed = cast(ProviderId, pid)
        if verb == "get":
            print(get_credentials(typed))
            return 0
        if verb == "add":
            cmd_add(typed)
            return 0
        if verb == "remove":
            cmd_remove(typed)
            return 0
        return cmd_check(typed)
    raise UsageError(f"unknown command '{verb}' (see --help)")


def main(argv: Sequence[str]) -> int:
    try:
        return run(argv)
    except ToolError as e:
        print(f"provider-registry: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("provider-registry: aborted", file=sys.stderr)
        return 1
