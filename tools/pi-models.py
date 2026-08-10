#!/usr/bin/env python3
"""pi-models — curate the per-provider model allowlists the Pi `live`
extension filters against (runtime: ~/.pi/agent/extensions/live/curated/).

Usage: pi-models [--provider nim|opencode] <command> [args...]

  list                print currently kept (curated) patterns, one per line
  add <id|glob>...    keep extra model(s) (exact id or glob, e.g. nvidia/nemotron-*)
  rm  <id|glob>...    stop keeping model(s) (exact pattern match)
  pick                fetch live catalog, numbered multi-select keep-set
  new                 advisory diff: live catalog vs curated ("not kept")

Env overrides (dev/test):
  PI_CURATED_DIR   curated dir (default: ~/.pi/agent/extensions/live/curated)
  PI_AUTH_JSON     auth.json (default: ~/.pi/agent/auth.json)

The key is read from auth.json (stored `nim`/`opencode` credential) and used
only for the live catalog fetch. Deployed to ~/.local/bin/pi-models by `make dev`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

CURATED_DIR = Path(
    os.environ.get(
        "PI_CURATED_DIR",
        str(Path.home() / ".pi" / "agent" / "extensions" / "live" / "curated"),
    )
)
AUTH_JSON = Path(
    os.environ.get("PI_AUTH_JSON", str(Path.home() / ".pi" / "agent" / "auth.json"))
)

ProviderId = Literal["opencode", "nim"]


@dataclass(frozen=True)
class Provider:
    base_url: str
    auth_key: str
    curated_file: str
    label: str


# id -> baseUrl|auth.json key|curated file|label
PROVIDERS: dict[ProviderId, Provider] = {
    "opencode": Provider(
        "https://opencode.ai/zen/v1", "opencode", "opencode.json", "OpenCode Zen"
    ),
    "nim": Provider(
        "https://integrate.api.nvidia.com/v1", "nim", "nim.json", "NVIDIA NIM (live)"
    ),
}
PROVIDER_ORDER: tuple[ProviderId, ...] = ("opencode", "nim")


class ToolError(Exception):
    """User-facing failure; the message is printed to stderr by main()."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class UsageError(ToolError):
    pass


class CatalogError(ToolError):
    pass


class AbortError(ToolError):
    pass


def usage() -> None:
    print(
        """pi-models — curate Pi provider model allowlists (live extension)

Usage: pi-models [--provider nim|opencode] <command> [args...]

Commands:
  list                print currently kept (curated) patterns
  add <id|glob>...    keep extra model(s) (exact id or glob)
  rm  <id|glob>...    stop keeping model(s)
  pick                fetch live catalog, numbered multi-select keep-set
  new                 advisory diff: live catalog vs curated ("not kept")

Env (dev/test): PI_CURATED_DIR, PI_AUTH_JSON"""
    )


def curated_file(prov: ProviderId) -> Path:
    return CURATED_DIR / PROVIDERS[prov].curated_file


def curated_file_exists(prov: ProviderId) -> None:
    path = curated_file(prov)
    if not path.exists():
        raise ToolError(
            f"curated file missing: {path} (deploy with 'make pi', or set"
            " PI_CURATED_DIR to dev/pi/extensions/live/curated for dev)"
        )


def load_auth() -> dict[str, object]:
    try:
        with AUTH_JSON.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return cast(dict[str, object], data)


def require_key(prov: ProviderId) -> str:
    cred = load_auth().get(PROVIDERS[prov].auth_key)
    if isinstance(cred, dict):
        key = cred.get("key")
        if isinstance(key, str) and key:
            return key
    raise ToolError(
        f"no API key for provider '{prov}' in {AUTH_JSON} — run: pi-auth --provider {prov}"
    )


def fetch_catalog(prov: ProviderId) -> list[str]:
    url = f"{PROVIDERS[prov].base_url}/models"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {require_key(prov)}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except OSError as e:
        raise CatalogError(f"GET {url} failed (offline or network restricted?)") from e
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as e:
        raise CatalogError(f"GET {url} returned an unexpected payload") from e
    if not isinstance(raw, dict):
        raise CatalogError(f"GET {url} returned an unexpected payload")
    data = cast(dict[str, object], raw)
    models = data.get("data")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        model_id = m.get("id")
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return ids


def as_patterns(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [p for p in value if isinstance(p, str) and p]


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


def read_patterns(prov: ProviderId) -> list[str]:
    path = curated_file(prov)
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError(f"could not parse {path}") from e
    if not isinstance(data, dict):
        raise ToolError(f"could not parse {path}")
    return as_patterns(data.get("patterns"))


def load_curated(path: Path, *, fail_verb: str) -> dict[str, object]:
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError(f"failed to {fail_verb} {path}") from e
    if not isinstance(data, dict):
        raise ToolError(f"failed to {fail_verb} {path}")
    return cast(dict[str, object], data)


def write_curated(path: Path, data: dict[str, object], *, fail_verb: str) -> None:
    try:
        atomic_write(path, json.dumps(data, indent=2) + "\n", 0o644)
    except OSError as e:
        raise ToolError(f"failed to {fail_verb} {path}") from e


def cmd_list(prov: ProviderId) -> None:
    curated_file_exists(prov)
    patterns = read_patterns(prov)
    if not patterns:
        print(f"(no patterns — the picker will show nothing for {prov})")
        return
    for pat in patterns:
        print(pat)


def cmd_add(prov: ProviderId, patterns: Sequence[str]) -> None:
    curated_file_exists(prov)
    if not patterns:
        raise UsageError("add requires at least one <id|glob>")
    for pat in patterns:
        if not pat:
            raise UsageError("empty pattern")
        if any(c.isspace() for c in pat):
            raise UsageError(f"invalid pattern '{pat}' (no whitespace)")
    path = curated_file(prov)
    data = load_curated(path, fail_verb="update")
    current = as_patterns(data.get("patterns"))
    existing = set(current)
    added = [p for p in patterns if p not in existing]
    data["patterns"] = sorted(set(current) | set(patterns))
    write_curated(path, data, fail_verb="update")
    print(f"added to {path} keep-set: {' '.join(added) if added else '(all already present)'}")


def cmd_rm(prov: ProviderId, targets: Sequence[str]) -> None:
    curated_file_exists(prov)
    if not targets:
        raise UsageError("rm requires at least one <id|glob>")
    path = curated_file(prov)
    data = load_curated(path, fail_verb="update")
    current = as_patterns(data.get("patterns"))
    target_set = set(targets)
    removed = [p for p in current if p in target_set]
    data["patterns"] = [p for p in current if p not in target_set]
    write_curated(path, data, fail_verb="update")
    print(
        f"removed from {path} keep-set:"
        f" {' '.join(removed) if removed else '(nothing matched)'}"
    )


def parse_selection(selection: str, count: int) -> list[int]:
    text = selection.strip()
    if text.lower() == "all":
        return list(range(1, count + 1))
    indices: set[int] = set()
    try:
        for tok in text.replace(",", " ").split():
            if not tok:
                continue
            if "-" in tok:
                start, end = tok.split("-", 1)
                indices.update(range(int(start), int(end) + 1))
            else:
                indices.add(int(tok))
    except ValueError:
        raise AbortError("invalid selection") from None
    return [i for i in sorted(indices) if 1 <= i <= count]


def cmd_pick(prov: ProviderId) -> None:
    curated_file_exists(prov)
    ids = fetch_catalog(prov)
    if not ids:
        raise CatalogError(f"live catalog for '{prov}' is empty")
    p = PROVIDERS[prov]
    print(f"{prov} ({p.label}) — {len(ids)} models from {p.base_url}")
    for n, model_id in enumerate(ids, 1):
        print(f"{n:3d}) {model_id}")
    try:
        selection = input(
            "Keep which models? (comma/space, ranges 1-5, 'all' = everything, blank = none): "
        )
    except EOFError:
        raise AbortError("aborted") from None
    chosen_ids = [ids[i - 1] for i in parse_selection(selection, len(ids))]
    path = curated_file(prov)
    data = load_curated(path, fail_verb="write")
    data["patterns"] = sorted(chosen_ids)
    write_curated(path, data, fail_verb="write")
    print(f"wrote {prov} keep-set ({len(chosen_ids)} patterns) to {path}")


def matches(model_id: str, pattern: str) -> bool:
    if "*" not in pattern:
        return model_id == pattern
    body = re.escape(pattern)
    body = body.replace(r"\*\*", "\u0000").replace(r"\*", "[^/]*").replace("\u0000", ".*")
    return re.fullmatch(body, model_id) is not None


def cmd_new(prov: ProviderId) -> None:
    curated_file_exists(prov)
    ids = fetch_catalog(prov)
    patterns = read_patterns(prov)
    not_kept = [m for m in ids if not any(matches(m, pat) for pat in patterns)]
    total = len(ids)
    if not not_kept:
        print(f"all {total} live {prov} models are already kept")
        return
    print(f"{len(not_kept)} of {total} live {prov} models are NOT kept:")
    for m in not_kept:
        print(m)


def parse_provider(value: str) -> ProviderId:
    if value not in PROVIDERS:
        raise UsageError(
            f"unknown provider '{value}' (expected: {' '.join(PROVIDER_ORDER)})"
        )
    return cast(ProviderId, value)


def run(argv: Sequence[str]) -> int:
    provider: ProviderId = "nim"
    positionals: list[str] = []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--provider":
            if not args:
                raise UsageError("--provider needs an argument")
            provider = parse_provider(args.pop(0))
        elif arg.startswith("--provider="):
            provider = parse_provider(arg.split("=", 1)[1])
        elif arg in ("-h", "--help"):
            usage()
            return 0
        else:
            positionals.append(arg)

    if not positionals:
        usage()
        return 2
    cmd = positionals[0]
    rest = positionals[1:]

    if cmd == "list":
        cmd_list(provider)
    elif cmd == "add":
        cmd_add(provider, rest)
    elif cmd == "rm":
        cmd_rm(provider, rest)
    elif cmd == "pick":
        cmd_pick(provider)
    elif cmd == "new":
        cmd_new(provider)
    else:
        raise UsageError(f"unknown command '{cmd}' (see --help)")
    return 0


def main(argv: Sequence[str]) -> int:
    try:
        return run(argv)
    except ToolError as e:
        print(f"pi-models: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("pi-models: aborted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
