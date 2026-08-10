#!/usr/bin/env python3
"""pi-auth — batch + validate Pi provider credentials (~/.pi/agent/auth.json).

Additive by default: prompts only for target providers with no auth.json
entry. A clean slate is explicit via --reset (backs up, wipes ALL registered
credentials incl. /login OAuth tokens, then re-prompts).

Usage:
  pi-auth                       prompt for missing providers (additive)
  pi-auth --provider <id>...    restrict to listed providers (repeatable)
  pi-auth --force               re-prompt even if already configured
  pi-auth --reset [--yes]       back up + wipe ALL, then prompt opencode/nim
  pi-auth check                 run `pi auth check --provider <id>` per target
  pi-auth -h|--help

Env override (dev/test): PI_AUTH_JSON (default: ~/.pi/agent/auth.json).

Validation: the catalog /models endpoints accept ANY bearer key (HTTP 200),
so the per-key check is reachability-only — a wrong key is caught on first
use / via `pi-auth check`. Values starting with "!" or containing "$" are
stored verbatim (Pi resolves !command / $ENV refs at read time) and skip the
reachability check.

Deployed to ~/.local/bin/pi-auth by `make dev`.
"""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast

AUTH_JSON = Path(
    os.environ.get("PI_AUTH_JSON", str(Path.home() / ".pi" / "agent" / "auth.json"))
)

ProviderId = Literal["opencode", "nim"]


@dataclass(frozen=True)
class Provider:
    label: str
    auth_key: str
    env_var: str
    endpoint: str


# id|label|auth.json key|env var|validate endpoint (empty = skip live check)
PROVIDERS: dict[ProviderId, Provider] = {
    "opencode": Provider(
        "OpenCode Zen",
        "opencode",
        "OPENCODE_API_KEY",
        "https://opencode.ai/zen/v1/models",
    ),
    "nim": Provider(
        "NVIDIA NIM (live)",
        "nim",
        "NVIDIA_NIM_API_KEY",
        "https://integrate.api.nvidia.com/v1/models",
    ),
}
PROVIDER_ORDER: tuple[ProviderId, ...] = ("opencode", "nim")


class ProviderEntry(TypedDict):
    type: str
    key: str


class ToolError(Exception):
    """User-facing failure; the message is printed to stderr by main()."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class UsageError(ToolError):
    pass


class AuthParseError(ToolError):
    pass


class AbortError(ToolError):
    pass


def usage() -> None:
    print(
        """pi-auth — batch + validate Pi provider credentials (auth.json)

Usage:
  pi-auth                       prompt for missing providers (additive)
  pi-auth --provider <id>...    restrict to listed providers
  pi-auth --force               re-prompt even if already configured
  pi-auth --reset [--yes]       back up + wipe ALL credentials, re-prompt
  pi-auth check                 run 'pi auth check --provider <id>' per target

Env (dev/test): PI_AUTH_JSON"""
    )


def warn_lock() -> None:
    lock = Path(str(AUTH_JSON) + ".lock")
    if lock.exists():
        print(
            f"pi-auth: WARNING: {lock} exists (Pi may be running or a stale lock)",
            file=sys.stderr,
        )


def load_auth() -> dict[str, object]:
    try:
        with AUTH_JSON.open() as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        raise AuthParseError(f"cannot parse {AUTH_JSON}") from e
    if not isinstance(data, dict):
        raise AuthParseError(f"cannot parse {AUTH_JSON}")
    return cast(dict[str, object], data)


def entry_exists(data: dict[str, object], prov: ProviderId) -> bool:
    return bool(data.get(prov))


def atomic_write(path: Path, content: str, mode: int) -> None:
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.tmp.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
        os.chmod(path, mode)
    except Exception:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        raise


def get_key(prov: ProviderId) -> str | None:
    p = PROVIDERS[prov]
    print(f"provider: {prov} ({p.label}) — env fallback: ${p.env_var}", file=sys.stderr)
    try:
        val = getpass.getpass(
            "  API key (blank = skip; '!cmd' or '$ENV' stored verbatim): "
        )
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None
    return val


def http_status(url: str, key: str) -> int:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def reachability_check(prov: ProviderId, key: str) -> None:
    p = PROVIDERS[prov]
    if not p.endpoint:
        print(f"pi-auth: no validate endpoint for {prov} — skipping live check")
        return
    if key.startswith("!") or "$" in key:
        print(
            f"pi-auth: {prov} key is a '!cmd'/'$ENV' reference — stored verbatim"
            " (Pi resolves); skipping live check"
        )
        return
    code = http_status(p.endpoint, key)
    if code == 200:
        print(f"pi-auth: {prov} endpoint reachable ({p.endpoint})")
        print(
            "  note: catalog endpoints accept any key — a wrong key is caught on first use"
        )
    else:
        print(
            f"pi-auth: WARNING: could not reach {p.endpoint} (HTTP {code:03d})"
            " — key written anyway; verify with 'pi-auth check'",
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
    print(f"pi-auth: wrote {prov} to {AUTH_JSON} (0600)")


def prompt_providers(targets: Sequence[ProviderId], *, force: bool) -> None:
    warn_lock()
    for prov in targets:
        if not force and entry_exists(load_auth(), prov):
            print(
                f"pi-auth: {prov} already configured — skipping (use --force to re-prompt)"
            )
            continue
        key = get_key(prov)
        if key is None:
            print(f"pi-auth: skipped {prov} (input closed)")
            continue
        if not key:
            print(f"pi-auth: skipped {prov} (no key entered)")
            continue
        reachability_check(prov, key)
        merge_write(prov, key)


def do_reset(targets: Sequence[ProviderId], *, yes: bool) -> None:
    warn_lock()
    backup = Path(str(AUTH_JSON) + ".bak")
    if AUTH_JSON.exists():
        if not yes:
            try:
                confirm = input(
                    f"Wipe ALL credentials in {AUTH_JSON}"
                    " (incl. /login OAuth) and re-prompt? [y/N] "
                )
            except EOFError:
                confirm = ""
            if confirm.strip() not in ("y", "Y"):
                raise AbortError("aborted")
        shutil.copy2(AUTH_JSON, backup)
        os.chmod(backup, 0o600)
        print(f"pi-auth: backed up {AUTH_JSON} -> {backup} (0600)")
    else:
        print(f"pi-auth: no {AUTH_JSON} to back up")
    try:
        atomic_write(AUTH_JSON, "{}\n", 0o600)
    except OSError as e:
        raise ToolError(f"failed to wipe {AUTH_JSON}") from e
    print(f"pi-auth: wiped all credentials from {AUTH_JSON}")
    prompt_providers(targets, force=True)


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
                f"     hint: '{prov}' is registered by the live extension — run 'make pi' first"
            )
    return rc


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


def run(argv: Sequence[str]) -> int:
    mode = "add"
    force = False
    reset = False
    yes = False
    selected: list[ProviderId] = []

    i = 0
    args = list(argv)
    while i < len(args):
        arg = args[i]
        if arg == "--provider":
            ids: list[str] = []
            j = i + 1
            while j < len(args) and not args[j].startswith("-"):
                ids.append(args[j])
                j += 1
            if not ids:
                raise UsageError("--provider needs at least one id")
            selected.extend(validate_ids(ids))
            i = j
            continue
        if arg.startswith("--provider="):
            selected.extend(validate_ids([arg.split("=", 1)[1]]))
        elif arg == "--force":
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
        i += 1

    targets: Sequence[ProviderId] = selected or PROVIDER_ORDER
    if reset:
        do_reset(targets, yes=yes)
    elif mode == "check":
        return cmd_check(targets)
    else:
        prompt_providers(targets, force=force)
    return 0


def main(argv: Sequence[str]) -> int:
    try:
        return run(argv)
    except ToolError as e:
        print(f"pi-auth: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("pi-auth: aborted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
