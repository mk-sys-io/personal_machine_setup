#!/usr/bin/env python3
"""pi — Pi provisioning: provider credentials + model allowlist discovery.

Subcommands:
  pi-setup auth                 prompt for missing providers (additive)
  pi-setup auth --provider <id>...    restrict to listed providers (repeatable)
  pi-setup auth --force         re-prompt even if already configured
  pi-setup auth --reset [--yes]       back up + wipe ALL credentials, re-prompt
  pi-setup auth check           run 'pi auth check --provider <id>' per target

  pi-setup probe [<provider>] [--write]   probe live catalog, report working models
  pi-setup dir                    list curated files in the live curated dir

After credential setup, `pi-setup auth` offers to probe the NVIDIA NIM catalog
and generate the curated allowlist the live extension filters against (runtime:
~/.pi/agent/extensions/live/curated/). NIM probing matters: the catalog lists
stale/retired models that 404 on chat requests — a 15 s Pi-like chat request
(tool-calling + streaming) is the only reliable availability check.

OpenCode Zen is deliberately NOT probed: the free-tier catalog is slow (up to
20 s+ first token) and Cloudflare blocks urllib UAs on the chat route, so any
realistic timeout would drop working models. Zen is a static curated list
(exact ids) shipped in dev/pi/extensions/live/curated/opencode.json and copied
into place by `make pi`; `probe opencode` refuses to run.

Catalog/auth GETs retry up to FETCH_RETRIES with backoff on transient network
errors and send an explicit User-Agent; HTTP-level rejections are reported as
their status code, never as "offline".

Env overrides (dev/test):
  PI_CURATED_DIR   curated dir (default: ~/.pi/agent/extensions/live/curated)
  PI_AUTH_JSON     auth.json (default: ~/.pi/agent/auth.json)

Deployed to ~/.local/bin/pi-setup by `make dev`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast

AUTH_JSON = Path(
    os.environ.get("PI_AUTH_JSON", str(Path.home() / ".pi" / "agent" / "auth.json"))
)
CURATED_DIR = Path(
    os.environ.get(
        "PI_CURATED_DIR",
        str(Path.home() / ".pi" / "agent" / "extensions" / "live" / "curated"),
    )
)

PROBE_TIMEOUT = 15  # seconds per model (fast-only: only responsive models survive)
PROBE_PACE = 1.5  # seconds between probes (NIM worker saturation / key RPM pacing)
HTTP_TIMEOUT = 15  # seconds per HTTP request
FETCH_RETRIES = 3  # catalog/auth GET attempts before giving up
RETRY_BACKOFF = (1, 2)  # seconds between retries

# Model ids that are categorically not for coding/agentic chat (embedding,
# moderation, translation, retrieval, etc.) — skipped before probing.
NON_CHAT_KEYWORDS = frozenset({
    "embed", "safety", "guard", "translate", "parse",
    "retriever", "clip", "diffusion", "video", "detector",
    "reward", "deplot",
    "imagen", "veo", "lyria", "tts", "audio",
    "deep-research", "robotics",
})


def is_non_chat(model_id: str) -> bool:
    """True if the model id matches a NON_CHAT_KEYWORDS token (not usable for agentic chat)."""
    return any(kw in model_id.lower() for kw in NON_CHAT_KEYWORDS)


# Minimal tool schema mirroring Pi's agent tools — proves the model accepts
# tool-calling request format (a model that breaks on tools is unusable in Pi).
PI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Bash command"}
                },
                "required": ["command"],
            },
        },
    }
]

# Words that terminate a --provider id list (never valid provider ids)
COMMAND_WORDS = frozenset({"auth", "dir", "check", "probe", "add", "rm", "list"})

ProviderId = Literal["opencode", "nim", "openrouter", "zai", "nararouter", "gemini"]


@dataclass(frozen=True)
class Provider:
    label: str
    auth_key: str
    env_var: str
    base_url: str
    curated_file: str
    endpoint: str
    chat_suffix: str = "/chat/completions"


# id|label|auth.json key|env var|base URL|curated file|validate endpoint
PROVIDERS: dict[ProviderId, Provider] = {
    "opencode": Provider(
        "OpenCode Zen",
        "opencode",
        "OPENCODE_API_KEY",
        "https://opencode.ai/zen/v1",
        "opencode.json",
        "https://opencode.ai/zen/v1/models",
    ),
    "nim": Provider(
        "NVIDIA NIM (live)",
        "nim",
        "NVIDIA_NIM_API_KEY",
        "https://integrate.api.nvidia.com/v1",
        "nim.json",
        "https://integrate.api.nvidia.com/v1/models",
    ),
    "openrouter": Provider(
        "OpenRouter (free)",
        "openrouter",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1",
        "openrouter-free.json",
        "https://openrouter.ai/api/v1/models",
    ),
    "zai": Provider(
        "Z.ai (free)",
        "zai",
        "ZAI_API_KEY",
        "https://api.z.ai/api/paas/v4",
        "zai.json",
        "https://api.z.ai/api/paas/v4/models",
    ),
    "nararouter": Provider(
        "NaraRouter (free)",
        "nararouter",
        "NARAROUTER_API_KEY",
        "https://router.bynara.id/v1",
        "nararouter-free.json",
        "https://router.bynara.id/api/plans",
    ),
    "gemini": Provider(
        "Google AI Studio (Gemini)",
        "gemini",
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini.json",
        "https://generativelanguage.googleapis.com/v1beta/models",
        chat_suffix="/openai/chat/completions",
    ),
}
PROVIDER_ORDER: tuple[ProviderId, ...] = (
    "opencode", "nim", "openrouter", "zai", "nararouter", "gemini",
)


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


class CatalogError(ToolError):
    pass


class AbortError(ToolError):
    pass


def usage() -> None:
    print(
        """pi-setup — Pi provisioning: credentials + model allowlist discovery

Usage:
  pi-setup auth [--provider <id>...] [--force|--reset [--yes]|check]
  pi-setup probe [<provider>] [--write]
  pi-setup dir

Commands:
  auth                prompt for missing providers (additive); after setup
                      offers to probe NIM + fetch free-model lists
  auth --force        re-prompt even if already configured
  auth --reset [--yes]  back up + wipe ALL credentials, re-prompt
  auth check          run 'pi auth check --provider <id>' per target
  probe [<provider>] [--write]  probe live catalog / fetch free-model lists;
                      summary + optionally write curated files (default: nim)
  dir                 list curated files in the live curated dir

Providers:
  opencode     OpenCode Zen (static curated list, no probe needed)
  nim          NVIDIA NIM (live probe)
  openrouter   OpenRouter free models (auto-fetched)
  zai          Z.ai free Flash models (auto-fetched)
  nararouter   NaraRouter free models (auto-fetched)
  gemini       Google AI Studio (live probe, 3-layer filter)

Env (dev/test): PI_AUTH_JSON, PI_CURATED_DIR"""
    )


def warn_lock() -> None:
    lock = Path(str(AUTH_JSON) + ".lock")
    if lock.exists():
        print(
            f"pi: WARNING: {lock} exists (Pi may be running or a stale lock)",
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


def require_key(prov: ProviderId) -> str:
    cred = load_auth().get(PROVIDERS[prov].auth_key)
    if isinstance(cred, dict):
        key = cred.get("key")
        if isinstance(key, str) and key:
            return key
    raise ToolError(
        f"no API key for provider '{prov}' in {AUTH_JSON} — run: pi-setup auth --provider {prov}"
    )


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


def curated_file(prov: ProviderId) -> Path:
    return CURATED_DIR / PROVIDERS[prov].curated_file


def write_curated(path: Path, data: dict[str, object], *, fail_verb: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=2) + "\n", 0o644)
    except OSError as e:
        raise ToolError(f"failed to {fail_verb} {path}") from e


# --- auth --------------------------------------------------------------


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


def _http_get(url: str, key: str, *, use_auth: bool = True) -> tuple[int, bytes | None, str]:
    """GET with optional auth header; returns (status, body, error reason).

    HTTP errors are final (no retry) and reported as their status code.
    Transient OSErrors (timeout/DNS/TLS) retry up to FETCH_RETRIES with
    backoff; after exhausting, status is 0 with the last error reason.
    When use_auth=False, no Authorization header is sent (for Google AI
    Studio catalog which uses ?key= query param instead).
    """
    reason = ""
    headers: dict[str, str] = {"User-Agent": "pi-setup/1.0"}
    if use_auth and key:
        headers["Authorization"] = f"Bearer {key}"
    for attempt in range(FETCH_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.status, resp.read(), ""
        except urllib.error.HTTPError as e:
            return e.code, None, ""
        except OSError as e:
            reason = str(e)
            if attempt < FETCH_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])
    return 0, None, reason


def http_status(url: str, key: str) -> int:
    """HTTP status for url, or 0 if the request failed after retries."""
    status, _, _ = _http_get(url, key)
    return status


def _fetch_json(url: str, key: str, *, use_auth: bool = True) -> dict:
    """GET url, parse JSON response. Raises CatalogError on failure."""
    status, body, reason = _http_get(url, key, use_auth=use_auth)
    if status == 0:
        raise CatalogError(
            f"GET {url} failed after {FETCH_RETRIES} attempts: {reason}"
        )
    if status != 200:
        raise CatalogError(f"GET {url} returned HTTP {status}")
    assert body is not None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CatalogError(f"GET {url} returned an unexpected payload") from e
    if not isinstance(raw, dict):
        raise CatalogError(f"GET {url} returned an unexpected payload")
    return raw


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
        print(f"pi: backed up {AUTH_JSON} -> {backup} (0600)")
    else:
        print(f"pi: no {AUTH_JSON} to back up")
    try:
        atomic_write(AUTH_JSON, "{}\n", 0o600)
    except OSError as e:
        raise ToolError(f"failed to wipe {AUTH_JSON}") from e
    print(f"pi: wiped all credentials from {AUTH_JSON}")
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


def maybe_probe(targets: Sequence[ProviderId]) -> None:
    probeable = cast(
        list[ProviderId],
        [p for p in targets if p in ("nim", "openrouter", "zai", "nararouter", "gemini")],
    )
    if not probeable:
        return
    configured = cast(
        list[ProviderId],
        [p for p in probeable if entry_exists(load_auth(), cast(ProviderId, p))],
    )
    if not configured:
        return
    for prov in configured:
        label = PROVIDERS[prov].label
        try:
            confirm = input(f"Probe {label}? [y/N] ")
        except EOFError:
            confirm = ""
        if confirm.strip().lower() in ("y", "yes"):
            cmd_probe(prov, write=True)


# --- models ------------------------------------------------------------


def fetch_catalog(prov: ProviderId) -> list[str]:
    url = f"{PROVIDERS[prov].base_url}/models"
    raw = _fetch_json(url, require_key(prov))
    models = raw.get("data")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        model_id = m.get("id")
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return sorted(set(ids))


def probe_model(prov: ProviderId, model_id: str, key: str, timeout: int) -> tuple[int, float]:
    """Probe with a Pi-like request. Returns (HTTP status, total seconds).

    Status 200 = working, 0 = network/protocol error (unusable body),
    -1 = total-time exceeded (too slow or completely unresponsive).
    """
    url = f"{PROVIDERS[prov].base_url}{PROVIDERS[prov].chat_suffix}"
    payload = json.dumps(
        {
            "model": model_id,
            "messages": [
                {"role": "user", "content": "What is 2+2? Reply with just the number."}
            ],
            "tools": PI_TOOLS,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": 1024,
        }
    ).encode()
    headers: dict[str, str] = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "pi-setup/1.0",
    }
    if prov == "nim":
        headers["X-BILLING-INVOKE-ORIGIN"] = "Pi"

    req = urllib.request.Request(url, data=payload, headers=headers)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks: list[bytes] = []
            while True:
                if time.time() - start > timeout:
                    break  # total-time exceeded — too slow to be usable
                chunk = resp.read(4096)
                if not chunk:
                    break  # stream complete
                chunks.append(chunk)
            body = b"".join(chunks)
            latency = time.time() - start
            if latency > timeout:
                return -1, latency
            if not body or b"data: " not in body:
                return 0, latency
            return resp.status, latency
    except urllib.error.HTTPError as e:
        return e.code, time.time() - start
    except OSError:
        return -1, time.time() - start


def filter_catalog(ids: list[str]) -> tuple[list[str], list[str]]:
    """Split ids into (to_probe, skipped_by_keyword)."""
    to_probe: list[str] = []
    skipped: list[str] = []
    for mid in ids:
        if is_non_chat(mid):
            skipped.append(mid)
        else:
            to_probe.append(mid)
    return to_probe, skipped


def fetch_free_models_openrouter() -> list[str]:
    """Fetch OpenRouter catalog; return free models (pricing=$0).

    Applies NON_CHAT_KEYWORDS pre-filter (checklist step 1) to exclude
    non-chat models (embedding, audio, safety, etc.) before pricing check.
    """
    raw = _fetch_json("https://openrouter.ai/api/v1/models", "")
    models = raw.get("data")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        if is_non_chat(mid):
            continue
        pricing = m.get("pricing")
        if not isinstance(pricing, dict):
            continue
        prompt_price = str(pricing.get("prompt", "1"))
        completion_price = str(pricing.get("completion", "1"))
        if prompt_price == "0" and completion_price == "0":
            ids.append(mid)
    return sorted(set(ids))



def fetch_free_models_nararouter() -> list[str]:
    """Fetch NaraRouter /api/plans; return free-tier model IDs."""
    raw = _fetch_json("https://router.bynara.id/api/plans", "")
    data = raw.get("data")
    if not isinstance(data, list) or not data:
        return []
    free_plan = data[0]
    if not isinstance(free_plan, dict):
        return []
    models = free_plan.get("models")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if isinstance(m, str) and m:
            ids.append(m)
    return sorted(set(ids))


def fetch_catalog_gemini() -> list[str]:
    """Fetch Google AI Studio native catalog; return chat-eligible model IDs.

    Uses the native models endpoint with ?key= auth (not Authorization header).
    Filters by supportedGenerationMethods containing 'generateContent' and
    excludes non-chat models via NON_CHAT_KEYWORDS.
    """
    key = require_key("gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    status, body, reason = _http_get(url, "", use_auth=False)
    if status == 0:
        raise CatalogError(
            f"GET {url} failed after {FETCH_RETRIES} attempts: {reason}"
        )
    if status != 200:
        raise CatalogError(f"GET {url} returned HTTP {status}")
    assert body is not None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise CatalogError(f"GET {url} returned an unexpected payload") from e
    models = raw.get("models")
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name", "")
        if not isinstance(name, str) or not name:
            continue
        # name field is "models/gemini-2.5-flash" — strip prefix
        model_id = name.removeprefix("models/")
        if not model_id:
            continue
        # Layer 1: supportedGenerationMethods must include generateContent
        methods = m.get("supportedGenerationMethods", [])
        if not isinstance(methods, list) or "generateContent" not in methods:
            continue
        # Layer 2: keyword exclusion for non-chat models
        if is_non_chat(model_id):
            continue
        ids.append(model_id)
    return sorted(set(ids))


def cmd_probe_free(prov: ProviderId, *, write: bool) -> None:
    """Fetch free-tier models for openrouter/nararouter and write curated file."""""
    if prov == "openrouter":
        ids = fetch_free_models_openrouter()
    elif prov == "nararouter":
        ids = fetch_free_models_nararouter()
    else:
        raise ToolError(f"'{prov}' does not support free-model discovery")
    p = PROVIDERS[prov]
    print(f"Fetching {p.label} free models...")
    if not ids:
        print(f"  {p.label}: no free models found")
        return
    print(f"  {p.label}: {len(ids)} free models")
    if write:
        path = curated_file(prov)
        write_curated(path, {"patterns": sorted(ids)}, fail_verb="write")
        print(f"Wrote {len(ids)} models to {path}")


def cmd_probe(prov: ProviderId, *, write: bool) -> None:
    if write and prov != "opencode":
        p = PROVIDERS[prov]
        if not p.endpoint:
            pass  # no catalog endpoint to check
        else:
            key_val = ""
            try:
                key_val = require_key(prov)
            except ToolError:
                pass
            if key_val and not key_val.startswith("!") and "$" not in key_val:
                status = http_status(p.endpoint, key_val)
                if status == 0:
                    raise ToolError(
                        f"no network connectivity to {p.endpoint} — check your internet connection"
                    )
    if prov == "opencode":
        raise ToolError(
            "'opencode' is not probed: Zen is a static curated list"
            " (dev/pi/extensions/live/curated/opencode.json). Edit the JSON"
            " directly and deploy with 'make pi'; probing is NIM-only."
        )
    if prov in ("openrouter", "nararouter"):
        cmd_probe_free(prov, write=write)
        return
    key = require_key(prov)
    if prov == "gemini":
        raw_ids = fetch_catalog_gemini()
    else:
        raw_ids = fetch_catalog(prov)
    if not raw_ids:
        raise CatalogError(f"live catalog for '{prov}' is empty")
    ids, skipped = filter_catalog(raw_ids)
    p = PROVIDERS[prov]
    skip_note = f", skipped {len(skipped)} non-chat" if skipped else ""
    print(f"Probing {p.label} ({len(ids)} models, timeout: {PROBE_TIMEOUT}s{skip_note})...")

    working: list[tuple[str, float]] = []
    for model_id in ids:
        status, latency = probe_model(prov, model_id, key, PROBE_TIMEOUT)
        if status == 200:
            working.append((model_id, latency))
        if model_id != ids[-1]:
            time.sleep(PROBE_PACE)

    avg = sum(lat for _, lat in working) / len(working) if working else 0
    print(
        f"  {p.label}: {len(working)} working (avg {avg:.1f}s)"
        f" of {len(ids)} probed"
    )
    if working:
        fast = sorted(working, key=lambda x: x[1])[:5]
        print(f"    Fastest: {', '.join(f'{m} ({lat:.1f}s)' for m, lat in fast)}")
    if write:
        path = curated_file(prov)
        write_curated(path, {"patterns": sorted(m for m, _ in working)}, fail_verb="write")
        print(f"Wrote {len(working)} models to {path}")


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


# --- cli ---------------------------------------------------------------


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
    targets: Sequence[ProviderId] = selected or PROVIDER_ORDER
    if reset:
        do_reset(targets, yes=yes)
        maybe_probe(targets)
    elif mode == "check":
        return cmd_check(targets)
    else:
        prompt_providers(targets, force=force)
        maybe_probe(targets)
    return 0


def run_probe(argv: Sequence[str]) -> int:
    provider: ProviderId = "nim"
    write = False
    for arg in argv:
        if arg == "--write":
            write = True
        elif arg in ("-h", "--help"):
            usage()
            return 0
        elif arg in PROVIDERS:
            provider = cast(ProviderId, arg)
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")
    cmd_probe(provider, write=write)
    return 0


def run_dir(argv: Sequence[str]) -> int:
    for arg in argv:
        if arg in ("-h", "--help"):
            usage()
            return 0
        else:
            raise UsageError(f"unknown argument '{arg}' (see --help)")
    cmd_dir()
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
    if not positionals:
        usage()
        return 2
    group = positionals[0]
    rest = positionals[1:]
    if group == "auth":
        return run_auth(rest, selected)
    if group == "probe":
        return run_probe(rest)
    if group == "dir":
        return run_dir(rest)
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
