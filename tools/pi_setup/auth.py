"""Credential flows: prompting, validation, reset/clean, 'pi auth check'."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence

from .config import AUTH_JSON, STORE_JSON
from .errors import AbortError, ToolError
from .fsio import (
    atomic_write,
    backup_file,
    entry_exists,
    load_auth,
    warn_lock,
    wipe_json,
)
from .http import http_status
from .probe import cmd_fetch_free, cmd_probe
from .providers import FETCH_PROVIDERS, PROVIDER_ORDER, PROVIDERS, ProviderId


def get_key(prov: ProviderId) -> str | None:
    p = PROVIDERS[prov]
    print(f"provider: {prov} ({p.label}) — env fallback: ${p.env_var}", file=sys.stderr)
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


def reachability_check(prov: ProviderId, key: str) -> None:
    p = PROVIDERS[prov]
    if not p.endpoint:
        print(f"pi: no validate endpoint for {prov} — skipping live check")
        return
    if key.startswith("!") or "$" in key:
        print(
            f"pi: {prov} key is a '!cmd'/'$ENV' reference — stored verbatim"
            " (Pi resolves); skipping live check"
        )
        return
    code = http_status(p.endpoint, key)
    if code == 200:
        print(f"pi: {prov} endpoint reachable ({p.endpoint})")
        print(
            "  note: catalog endpoints accept any key — a wrong key is caught on first use"
        )
    else:
        print(
            f"pi: WARNING: could not reach {p.endpoint} (HTTP {code:03d})"
            " — key written anyway; verify with 'pi-setup auth check'",
            file=sys.stderr,
        )


def merge_write(prov: ProviderId, key: str) -> None:
    data = load_auth()
    data[prov] = {"type": "api_key", "key": key}
    ordered = {k: data[k] for k in sorted(data)}
    try:
        atomic_write(AUTH_JSON, json.dumps(ordered, indent=2) + "\n", 0o600)
    except OSError as e:
        raise ToolError(f"failed to write {AUTH_JSON}") from e
    print(f"pi: wrote {prov} to {AUTH_JSON} (0600)")


def prompt_providers(targets: Sequence[ProviderId], *, force: bool) -> None:
    warn_lock()
    for prov in targets:
        if not force and entry_exists(load_auth(), prov):
            print(f"pi: {prov} already configured — skipping (use --force to re-prompt)")
            continue
        key = get_key(prov)
        if key is None:
            print(f"pi: skipped {prov} (input closed)")
            continue
        if not key:
            print(f"pi: skipped {prov} (no key entered)")
            continue
        reachability_check(prov, key)
        merge_write(prov, key)


def do_reset(targets: Sequence[ProviderId], *, yes: bool) -> None:
    warn_lock()
    selective = set(targets) != set(PROVIDER_ORDER)
    if selective:
        question = (
            f"Remove {', '.join(targets)} from {AUTH_JSON}"
            " and re-prompt? [y/N] "
        )
    else:
        question = (
            f"Wipe ALL credentials in {AUTH_JSON}"
            " (incl. /login OAuth) and re-prompt? [y/N] "
        )
    if AUTH_JSON.exists():
        if not yes:
            try:
                confirm = input(question)
            except EOFError:
                confirm = ""
            if confirm.strip() not in ("y", "Y"):
                raise AbortError("aborted")
        backup = backup_file(AUTH_JSON)
        if backup is not None:
            print(f"pi: backed up {AUTH_JSON} -> {backup} (0600)")
    else:
        print(f"pi: no {AUTH_JSON} to back up")
    if selective:
        data = load_auth()
        dropped = [prov for prov in targets if prov in data]
        for prov in dropped:
            del data[prov]
        ordered = {k: data[k] for k in sorted(data)}
        try:
            atomic_write(AUTH_JSON, json.dumps(ordered, indent=2) + "\n", 0o600)
        except OSError as e:
            raise ToolError(f"failed to update {AUTH_JSON}") from e
        print(f"pi: removed {len(dropped)} provider(s) from {AUTH_JSON}")
    else:
        try:
            wipe_json(AUTH_JSON)
        except OSError as e:
            raise ToolError(f"failed to wipe {AUTH_JSON}") from e
        print(f"pi: wiped all credentials from {AUTH_JSON}")
    prompt_providers(targets, force=True)


def do_clean(*, yes: bool) -> None:
    """Wipe ALL provider configs in one go: auth.json + models-store.json.

    Complements Pi's interactive /logout (per-provider): this is the scripted
    clean-slate path. Also purges cached model catalogs — stale store entries
    are what resurrects full (unfiltered) catalogs after a failed refresh.
    Curated allowlists are NOT touched (they are pi-setup outputs, not creds).
    """
    warn_lock()
    existing = [p for p in (AUTH_JSON, STORE_JSON) if p.exists()]
    if not existing:
        print("pi: nothing to clean (no auth.json / models-store.json)")
        return
    if not yes:
        listing = "\n".join(f"  {p}" for p in existing)
        try:
            confirm = input(
                f"Wipe ALL provider configs?\n{listing}\n"
                "Backups written to <file>.bak. Proceed? [y/N] "
            )
        except EOFError:
            confirm = ""
        if confirm.strip() not in ("y", "Y"):
            raise AbortError("aborted")
    for path in existing:
        backup = backup_file(path)
        if path is STORE_JSON:
            # Pure cache (model catalogs) — Pi recreates it on next refresh.
            try:
                os.unlink(path)
            except OSError as e:
                raise ToolError(f"failed to remove {path}") from e
            print(f"pi: removed {path} (backup: {backup}, 0600)")
        else:
            try:
                wipe_json(path)
            except OSError as e:
                raise ToolError(f"failed to wipe {path}") from e
            print(f"pi: wiped {path} (backup: {backup}, 0600)")


def parse_check(out: str) -> tuple[str, str]:
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return out, ""
    if not isinstance(data, dict):
        return out, ""
    return str(data.get("status", "?")), str(data.get("reason", ""))


def cmd_check(targets: Sequence[ProviderId]) -> int:
    rc = 0
    for prov in targets:
        try:
            proc = subprocess.run(
                ["pi", "auth", "check", "--provider", prov, "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:
            raise ToolError("'pi' not found in PATH — is Pi installed?") from e
        out = proc.stdout + proc.stderr
        if proc.returncode == 0:
            print(f"OK   {prov}: ready")
            continue
        rc = 1
        status, reason = parse_check(out)
        print(f"FAIL {prov}: {status}" + (f" ({reason})" if reason else ""))
        if reason == "provider_not_found":
            print(
                f"     hint: '{prov}' is registered by the live extension — run 'make dev' first"
            )
    return rc


def maybe_discover(targets: Sequence[ProviderId]) -> None:
    # Every provider is probeable/fetchable — nothing is static-only.
    # Prompt verb matches the strategy: probe = live requests, fetch = list.
    configured: list[ProviderId] = [
        p for p in targets if entry_exists(load_auth(), p)
    ]
    if not configured:
        return
    for prov in configured:
        label = PROVIDERS[prov].label
        if prov in FETCH_PROVIDERS:
            prompt = f"Fetch {label} free-model list? [y/N] "
            discover = cmd_fetch_free
        else:
            prompt = f"Probe {label}? [y/N] "
            discover = cmd_probe
        try:
            confirm = input(prompt)
        except EOFError:
            confirm = ""
        if confirm.strip().lower() in ("y", "yes"):
            discover(prov, write=True)
