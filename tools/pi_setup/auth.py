"""Credential flows: gopass→Pi copy, clear, manifest generation, 'pi auth check'."""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence

from provider_registry.config import GOPASS_ENTRY_PREFIX, gopass_show_field
from provider_registry.errors import ToolError as RegistryToolError

from .config import AUTH_JSON, PI_PROVIDERS_JSON, STORE_JSON
from .errors import ToolError
from .fsio import (
    atomic_write,
    load_auth,
    warn_lock,
    wipe_json,
)
from .manifest import render_manifest
from .providers import PROVIDERS, ProviderId, _resolved_strategy


def copy_vault_to_pi(targets: Sequence[ProviderId]) -> None:
    """Copy gopass credentials → Pi's auth.json (additive, skip no-cred, preserve OAuth).

    The gopass store is the single source of truth. This copies entries into
    Pi's deployment target, preserving any existing OAuth/other entries.
    """
    warn_lock()
    data = load_auth()
    copied = 0
    for prov in targets:
        try:
            key = gopass_show_field(f"{GOPASS_ENTRY_PREFIX}/{prov}", "key")
        except RegistryToolError as e:
            raise ToolError(str(e)) from e
        if key is None:
            continue
        data[prov] = {"type": "api_key", "key": key}
        copied += 1
    ordered = {k: data[k] for k in sorted(data)}
    try:
        atomic_write(AUTH_JSON, json.dumps(ordered, indent=2) + "\n", 0o600)
    except OSError as e:
        raise ToolError(f"failed to write {AUTH_JSON}") from e
    if copied:
        print(f"pi: copied {copied} credential(s) to {AUTH_JSON} (0600)")
    else:
        print("pi: no gopass credentials to copy")


def clear() -> None:
    """One-pass wipe of Pi's auth.json + models-store.json (gopass store kept).

    No flags, no confirmation — the gopass store remains the source of truth.
    """
    warn_lock()
    existing = [p for p in (AUTH_JSON, STORE_JSON) if p.exists()]
    if not existing:
        print("pi: nothing to clear (no auth.json / models-store.json)")
        return
    for path in existing:
        if path is STORE_JSON:
            try:
                os.unlink(path)
            except OSError as e:
                raise ToolError(f"failed to remove {path}") from e
            print(f"pi: removed {path}")
        else:
            try:
                wipe_json(path)
            except OSError as e:
                raise ToolError(f"failed to wipe {path}") from e
            print(f"pi: wiped {path}")
    print("pi: gopass store untouched (source of truth)")


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
    """Offer probe/fetch discovery for each configured provider."""
    configured: list[ProviderId] = []
    for p in targets:
        try:
            key = gopass_show_field(f"{GOPASS_ENTRY_PREFIX}/{p}", "key")
        except RegistryToolError as e:
            raise ToolError(str(e)) from e
        if key is not None:
            configured.append(p)
    if not configured:
        return
    for prov in configured:
        label = PROVIDERS[prov].label
        if _resolved_strategy(prov) == "fetch":
            prompt = f"Fetch {label} free-model list? [y/N] "
            from .probe import cmd_fetch_free
            discover = cmd_fetch_free
        else:
            prompt = f"Probe {label}? [y/N] "
            from .probe import cmd_probe
            discover = cmd_probe
        try:
            confirm = input(prompt)
        except EOFError:
            confirm = ""
        if confirm.strip().lower() in ("y", "yes"):
            discover(prov, write=True)


def generate_manifest() -> None:
    """Generate the Pi extension providers.json manifest (empty-list fallback on failure)."""
    try:
        PI_PROVIDERS_JSON.parent.mkdir(parents=True, exist_ok=True)
        PI_PROVIDERS_JSON.write_text(render_manifest())
        print(f"pi: wrote manifest to {PI_PROVIDERS_JSON}")
    except Exception as e:
        # Fallback: write empty providers list
        try:
            PI_PROVIDERS_JSON.parent.mkdir(parents=True, exist_ok=True)
            PI_PROVIDERS_JSON.write_text(json.dumps({"providers": []}, indent=2) + "\n")
        except Exception:
            pass
        print(f"pi: manifest generation failed ({e}), wrote empty fallback")
