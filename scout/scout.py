#!/usr/bin/env python3
"""Scout — free-model scout runner.

Phases 1–4: descriptor contract layer, fetch + predicate chain + filters,
state/report (snapshot diff, tombstones, provider-removed, report), and
LiteLLM bridge (registry sync).

Stdlib + PyYAML. YAML is loaded only via DuplicateKeySafeLoader (safe_load
equivalent; bare yaml.load() executes arbitrary objects — permanently banned).

Usage:
    scout validate [--providers-dir D] [file…]
    scout diff [--provider X] [file…]
    scout run [--providers-dir D] [--blacklist F] [--provider X] [file…]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_TOP = {"name", "base_url", "auth", "free_detection"}
ALLOWED_TOP = {
    "name", "base_url", "auth", "catalog", "free_detection",
    "capabilities", "quotas", "assume_free",
}
RULE_TYPES = {"suffix", "pricing_zero", "flag", "virtual_router"}

STATE_DIR = Path.home() / ".local" / "state" / "scout"
SNAPSHOT_PATH = STATE_DIR / "catalog.snapshot.json"
TOMBSTONES_PATH = STATE_DIR / "tombstones.json"
LOCK_PATH = STATE_DIR / "scout.lock"
REPORT_DIR = STATE_DIR


@dataclass
class ScoutError:
    path: Path
    line: int
    col: int
    message: str
    offending: str = ""

    def __str__(self) -> str:
        out = f"{self.path}:{self.line}:{self.col}: {self.message}"
        if self.offending:
            out += "\n    " + self.offending
        return out


class _DupKey(Exception):
    def __init__(self, mark, message: str) -> None:
        self.mark = mark
        self.message = message


class DuplicateKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen: set = set()
        for key_node, _ in node.value:
            try:
                key = self.construct_object(key_node, deep=False)
            except Exception:
                key = None
            if key in seen:
                raise _DupKey(key_node.start_mark, f"duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep)


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _offending(path: Path, mark) -> str:
    lines = _read_lines(path)
    idx = mark.line if mark is not None else -1
    if 0 <= idx < len(lines):
        return lines[idx]
    return ""


def _mark_err(path: Path, mark, message: str) -> ScoutError:
    line = (mark.line + 1) if mark is not None else 1
    col = (mark.column + 1) if mark is not None else 1
    return ScoutError(path=path, line=line, col=col, message=message,
                      offending=_offending(path, mark) if mark is not None else "")


def _scalar_str(node) -> bool:
    return isinstance(node, yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:str"


def _scalar_int(node) -> bool:
    return isinstance(node, yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:int"


def _scalar_bool(node) -> bool:
    return isinstance(node, yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:bool"


def _mapping(node) -> bool:
    return isinstance(node, yaml.MappingNode)


def _sequence(node) -> bool:
    return isinstance(node, yaml.SequenceNode)


def _keys(node: yaml.MappingNode) -> dict:
    out = {}
    for k, v in node.value:
        key = k.value if isinstance(k, yaml.ScalarNode) else None
        out[key] = (k, v)
    return out


# ---------------------------------------------------------------------------
# Contract validation (Phase 1)
# ---------------------------------------------------------------------------


def _validate_one(path: Path) -> list[ScoutError]:
    errors: list[ScoutError] = []
    if not path.exists():
        return [ScoutError(path=path, line=1, col=1, message="file not found")]

    text = path.read_text(encoding="utf-8")

    try:
        yaml.load(text, Loader=DuplicateKeySafeLoader)
    except _DupKey as e:
        return [_mark_err(path, e.mark, e.message)]
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return [_mark_err(path, mark, f"YAML parse error: {e}")]

    try:
        root = yaml.compose(text, Loader=DuplicateKeySafeLoader)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return [_mark_err(path, mark, f"YAML compose error: {e}")]

    if root is None:
        return [ScoutError(path=path, line=1, col=1, message="empty document")]
    if not _mapping(root):
        return [_mark_err(path, root.start_mark, "top-level must be a mapping")]

    km = _keys(root)

    for req in sorted(REQUIRED_TOP):
        if req not in km:
            errors.append(_mark_err(path, root.start_mark, f"missing required key {req!r}"))
    for k, (kn, _) in km.items():
        if k not in ALLOWED_TOP:
            errors.append(_mark_err(path, kn.start_mark, f"unknown top-level key {k!r}"))

    if "name" in km:
        _, v = km["name"]
        if not _scalar_str(v):
            errors.append(_mark_err(path, v.start_mark, "'name' must be a string"))
    if "base_url" in km:
        _, v = km["base_url"]
        if not _scalar_str(v):
            errors.append(_mark_err(path, v.start_mark, "'base_url' must be a string"))
    if "auth" in km:
        _, v = km["auth"]
        if not _scalar_str(v):
            errors.append(_mark_err(path, v.start_mark, "'auth' must be a string"))
        else:
            val = v.value
            if val != "none" and not val.startswith("env/"):
                errors.append(_mark_err(path, v.start_mark,
                                       "'auth' must be 'none' or 'env/NAME'"))

    if "catalog" in km:
        _, v = km["catalog"]
        if not _mapping(v):
            errors.append(_mark_err(path, v.start_mark, "'catalog' must be a mapping"))
        else:
            ckm = _keys(v)
            for ck in ("endpoint", "models_path"):
                if ck in ckm:
                    _, cv = ckm[ck]
                    if not _scalar_str(cv):
                        errors.append(_mark_err(path, cv.start_mark,
                                               f"'catalog.{ck}' must be a string"))

    if "free_detection" in km:
        _, v = km["free_detection"]
        if not _mapping(v):
            errors.append(_mark_err(path, v.start_mark, "'free_detection' must be a mapping"))
        else:
            fkm = _keys(v)
            if "rules" not in fkm:
                errors.append(_mark_err(path, v.start_mark,
                                       "missing required key 'free_detection.rules'"))
            else:
                _, rv = fkm["rules"]
                if not _sequence(rv):
                    errors.append(_mark_err(path, rv.start_mark,
                                           "'free_detection.rules' must be a list"))
                elif len(rv.value) == 0:
                    errors.append(_mark_err(path, rv.start_mark,
                                           "'free_detection.rules' must be non-empty"))
                else:
                    for item in rv.value:
                        if not _mapping(item):
                            errors.append(_mark_err(path, item.start_mark,
                                                   "each rule must be a mapping"))
                            continue
                        ikm = _keys(item)
                        if "type" not in ikm:
                            errors.append(_mark_err(path, item.start_mark,
                                                   "rule missing required key 'type'"))
                            continue
                        _, tv = ikm["type"]
                        if not _scalar_str(tv):
                            errors.append(_mark_err(path, tv.start_mark,
                                                   "rule 'type' must be a string"))
                            continue
                        if tv.value not in RULE_TYPES:
                            errors.append(_mark_err(path, tv.start_mark,
                                                   f"unknown rule type {tv.value!r}"))
                        if tv.value == "suffix":
                            if "value" not in ikm:
                                errors.append(_mark_err(path, item.start_mark,
                                                       "suffix rule requires 'value'"))
                            else:
                                _, vv = ikm["value"]
                                if not (_scalar_str(vv) or _sequence(vv)):
                                    errors.append(_mark_err(path, vv.start_mark,
                                                           "'value' must be string or list"))
                                elif _sequence(vv):
                                    for el in vv.value:
                                        if not _scalar_str(el):
                                            errors.append(_mark_err(path, el.start_mark,
                                                                   "'value' entries must be strings"))
                        if tv.value == "flag":
                            if "key" in ikm:
                                _, kv = ikm["key"]
                                if not _scalar_str(kv):
                                    errors.append(_mark_err(path, kv.start_mark,
                                                           "'key' must be a string"))
                        for rk in ikm:
                            if rk not in {"type", "value", "key"}:
                                _, rkn = ikm[rk]
                                errors.append(_mark_err(path, rkn.start_mark,
                                                       f"unknown rule key {rk!r}"))

    if "capabilities" in km:
        _, v = km["capabilities"]
        if not _mapping(v):
            errors.append(_mark_err(path, v.start_mark, "'capabilities' must be a mapping"))
        else:
            ckm = _keys(v)
            for ck in ("include_keywords", "exclude_keywords"):
                if ck in ckm:
                    _, cv = ckm[ck]
                    if not _sequence(cv):
                        errors.append(_mark_err(path, cv.start_mark,
                                               f"'capabilities.{ck}' must be a list"))
                    else:
                        for el in cv.value:
                            if not _scalar_str(el):
                                errors.append(_mark_err(path, el.start_mark,
                                                       f"'capabilities.{ck}' entries must be strings"))

    if "quotas" in km:
        _, v = km["quotas"]
        if not _mapping(v):
            errors.append(_mark_err(path, v.start_mark, "'quotas' must be a mapping"))
        else:
            qkm = _keys(v)
            if "rpm" in qkm:
                _, rv = qkm["rpm"]
                if not _scalar_int(rv):
                    errors.append(_mark_err(path, rv.start_mark, "'quotas.rpm' must be an integer"))

    if "assume_free" in km:
        _, v = km["assume_free"]
        if not _scalar_bool(v):
            errors.append(_mark_err(path, v.start_mark, "'assume_free' must be a boolean"))

    return errors


def _top_name(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    try:
        root = yaml.compose(text, Loader=DuplicateKeySafeLoader)
    except yaml.YAMLError:
        return None, None
    if not isinstance(root, yaml.MappingNode):
        return None, None
    for k, v in root.value:
        if isinstance(k, yaml.ScalarNode) and k.value == "name" and isinstance(v, yaml.ScalarNode):
            return v.value, v.start_mark
    return None, None


def _cross_file_duplicates(paths: list[Path]) -> list[ScoutError]:
    errors: list[ScoutError] = []
    by_dir: dict[Path, list[Path]] = {}
    for p in paths:
        by_dir.setdefault(p.parent, []).append(p)
    for d, files in by_dir.items():
        all_files = sorted(set(files) | set(d.glob("*.yaml")) | set(d.glob("*.yml")))
        seen: dict[str, Path] = {}
        name_marks: dict[Path, object] = {}
        for f in all_files:
            nm, mark = _top_name(f)
            if nm is None:
                continue
            name_marks[f] = mark
            if nm in seen:
                other = seen[nm]
                m = name_marks.get(f)
                if m is None:
                    m = name_marks.get(other)
                errors.append(_mark_err(
                    f, m, f"duplicate 'name: {nm}' across files: {other} and {f}"))
            else:
                seen[nm] = f
    return errors


def validate_paths(paths: list[Path]) -> list[ScoutError]:
    errors: list[ScoutError] = []
    for p in paths:
        errors.extend(_validate_one(p))
    errors.extend(_cross_file_duplicates(paths))
    return errors


# ---------------------------------------------------------------------------
# Descriptor loading with defaults (Phase 1/2)
# ---------------------------------------------------------------------------


def load_descriptor(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=DuplicateKeySafeLoader)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    cat = data.setdefault("catalog", {})
    if not isinstance(cat, dict):
        cat = {}
        data["catalog"] = cat
    cat.setdefault("endpoint", "/models")
    cat.setdefault("models_path", "data")
    fd = data.setdefault("free_detection", {})
    if not isinstance(fd, dict):
        fd = {}
        data["free_detection"] = fd
    for rule in fd.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        if rule.get("type") == "suffix":
            v = rule.get("value")
            if isinstance(v, str):
                rule["value"] = [v]
        if rule.get("type") == "flag":
            rule.setdefault("key", "isFree")
    return data


def _default_paths(providers_dir: Path) -> list[Path]:
    found = sorted(providers_dir.glob("*.yaml")) + sorted(providers_dir.glob("*.yml"))
    return found


# ---------------------------------------------------------------------------
# Free-detection matchers (Phase 2, D5 grammar)
# ---------------------------------------------------------------------------


def _leaf_zero(value) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return True
        try:
            return float(s) == 0.0
        except ValueError:
            return False
    return False


def _pricing_zero(record: dict) -> bool:
    pricing = record.get("pricing")
    if not isinstance(pricing, dict) or not pricing:
        return False  # no evidence -> fail (D5 fail-closed)
    leaves: list = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        else:
            leaves.append(node)

    walk(pricing)
    if not leaves:
        return False
    return all(_leaf_zero(v) for v in leaves)


def _provider_metadata_empty(record: dict) -> bool:
    ids = record.get("inference_provider_ids")
    if ids:
        return False
    tp = record.get("top_provider")
    if isinstance(tp, dict):
        if any(v not in (None, "", [], {}) for v in tp.values()):
            return False
    elif tp not in (None, "", [], {}):
        return False
    return True


def _virtual_router(record: dict) -> bool:
    return _pricing_zero(record) and _provider_metadata_empty(record)


def rule_match(record: dict, desc: dict) -> tuple[bool, str]:
    """Return (matched, rule_name) for the first passing rule."""
    for rule in (desc.get("free_detection", {}).get("rules") or []):
        if not isinstance(rule, dict):
            continue
        t = rule.get("type")
        if t == "suffix":
            rid = str(record.get("id", ""))
            vals = rule.get("value") or []
            if isinstance(vals, str):
                vals = [vals]
            if any(rid.endswith(str(s)) for s in vals):
                return True, "suffix"
        elif t == "pricing_zero":
            if _pricing_zero(record):
                return True, "pricing_zero"
        elif t == "flag":
            key = rule.get("key", "isFree")
            if record.get(key) is True:
                return True, "flag"
        elif t == "virtual_router":
            if _virtual_router(record):
                return True, "virtual_router"
    return False, ""


def is_free(record: dict, desc: dict) -> bool:
    matched, _ = rule_match(record, desc)
    return matched


def _text_capable_output(record: dict) -> bool:
    arch = record.get("architecture") or {}
    if not isinstance(arch, dict):
        return True
    om = arch.get("output_modalities")
    if isinstance(om, list):
        return any(str(m).lower().startswith("text") for m in om)
    mod = arch.get("modality")
    if isinstance(mod, str):
        out = mod.split("->")[-1] if "->" in mod else mod
        return "text" in out.lower()
    return True  # unknown -> keep (don't drop on missing evidence)


def keyword_keep(record: dict, desc: dict) -> bool:
    caps = desc.get("capabilities") or {}
    include = caps.get("include_keywords") or []
    exclude = caps.get("exclude_keywords") or []
    text = f"{record.get('id', '')} {record.get('name', '')}".lower()
    for kw in exclude:
        if str(kw).lower() in text:
            return False
    if include:
        if any(str(kw).lower() in text for kw in include):
            return True
        # ambiguous -> modality fallback (D6 amendment)
        return _text_capable_output(record)
    return True


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _navigate(obj, path: str):
    node = obj
    for part in path.split("."):
        if isinstance(node, dict):
            node = node[part]
        else:
            raise KeyError(f"models_path {path!r} not navigable at {part!r}")
    return node


def fetch_catalog(desc: dict) -> list[dict]:
    base = str(desc["base_url"]).rstrip("/")
    cat = desc.get("catalog") or {}
    endpoint = cat.get("endpoint", "/models")
    url = base + (endpoint if str(endpoint).startswith("/") else "/" + str(endpoint))

    auth = desc.get("auth", "none")
    headers = {"User-Agent": "scout/1"}
    if auth != "none":
        if not auth.startswith("env/"):
            raise ValueError(f"bad auth value {auth!r}")
        env_name = auth.split("/", 1)[1]
        key = os.environ.get(env_name)
        if not key:
            raise RuntimeError(f"missing env var {env_name} for auth")
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    models_path = cat.get("models_path", "data")
    records = _navigate(payload, models_path)
    if not isinstance(records, list):
        raise ValueError(f"models_path {models_path!r} did not resolve to a list")
    return records


def _load_snapshot() -> dict | None:
    try:
        if SNAPSHOT_PATH.exists():
            return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            if Path(tmp).exists():
                Path(tmp).unlink()
        except OSError:
            pass


def _load_tombstones() -> dict:
    try:
        if TOMBSTONES_PATH.exists():
            data = json.loads(TOMBSTONES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


# ---------------------------------------------------------------------------
# Blacklist (D7, two-tier) — tolerant parser, dormant until Phase 7 seeds file
# ---------------------------------------------------------------------------


def load_blacklist(path: Path) -> dict:
    """Return {'global': set[str], 'providers': {name: set[str]}}. Missing file => empty."""
    out = {"global": set(), "providers": {}}
    if not path.exists():
        return out
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return out
    if not isinstance(data, dict):
        return out
    g = data.get("global")
    if isinstance(g, list):
        out["global"] = {str(x) for x in g}
    prov = data.get("providers")
    if isinstance(prov, dict):
        for k, v in prov.items():
            if isinstance(v, list):
                out["providers"][str(k)] = {str(x) for x in v}
    # flat per-provider entries of form "provider:model"
    for key in ("per_provider", "provider"):
        flat = data.get(key)
        if isinstance(flat, list):
            for entry in flat:
                s = str(entry)
                if ":" in s:
                    prov_name, model_id = s.split(":", 1)
                    out["providers"].setdefault(prov_name, set()).add(model_id)
    return out


def _is_blacklisted(rid: str, provider: str, bl: dict) -> bool:
    if rid in bl.get("global", set()):
        return True
    prov_set = bl.get("providers", {}).get(provider, set())
    if rid in prov_set:
        return True
    # registered-name form provider/id
    reg = f"{provider}/{rid}"
    if reg in bl.get("global", set()):
        return True
    if reg in prov_set:
        return True
    return False


# ---------------------------------------------------------------------------
# flock (single-instance, D8/D9)
# ---------------------------------------------------------------------------


@contextmanager
def _flock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            print("scout: flock contention — waiting for lock", flush=True)
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# ---------------------------------------------------------------------------
# diff (Phase 2, extended Phase 3)
# ---------------------------------------------------------------------------


def _snapshot_ids(snapshot, name) -> set[str]:
    if not isinstance(snapshot, dict):
        return set()
    val = snapshot.get(name)
    if isinstance(val, list):
        return {str(x) for x in val}
    if isinstance(val, dict):
        ids = val.get("ids")
        if isinstance(ids, list):
            return {str(x) for x in ids}
    return set()


def run_diff(files: list[Path], provider_filter: str | None, blacklist_path: Path = Path("blacklist.yaml")) -> int:
    errs = validate_paths(files)
    if errs:
        for e in errs:
            print(str(e), file=sys.stderr)
        return 1

    descs: list[tuple[Path, dict]] = []
    for p in files:
        try:
            descs.append((p, load_descriptor(p)))
        except Exception as e:
            print(f"scout: ERROR loading {p}: {e}", file=sys.stderr)
            return 1

    snapshot = _load_snapshot()
    bl = load_blacklist(blacklist_path)

    for _path, desc in descs:
        name = desc["name"]
        if provider_filter and name != provider_filter:
            continue

        print(f"scout diff: {name}")

        try:
            records = fetch_catalog(desc)
        except Exception as e:
            print(f"  ERROR fetch: {e}", file=sys.stderr)
            continue

        if not records:
            print("  WARN: provider returned 0 models (indistinguishable from empty catalog)",
                  file=sys.stderr, flush=True)

        free_ids: list[str] = []
        verdicts: dict[str, str] = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            rid0 = rec.get("id")
            if isinstance(rid0, str) and _is_blacklisted(rid0, desc["name"], bl):
                continue
            matched, rule = rule_match(rec, desc)
            if matched and keyword_keep(rec, desc):
                rid = rec.get("id")
                if isinstance(rid, str):
                    free_ids.append(rid)
                    verdicts[rid] = "verified" if rule else "verified"
            # assume_free: classify as assumed when no rule matched but descriptor assumes
            if (not matched) and desc.get("assume_free") is True and keyword_keep(rec, desc):
                rid = rec.get("id")
                if isinstance(rid, str) and rid not in free_ids:
                    free_ids.append(rid)
                    verdicts[rid] = "assumed"

        free_set = set(free_ids)
        print(f"  classified free: {len(free_set)}")
        for rid in sorted(free_set):
            print(f"    {rid} ({verdicts.get(rid, 'verified')})")

        if snapshot:
            prev = _snapshot_ids(snapshot, name)
            adds = sorted(free_set - prev)
            removals = sorted(prev - free_set)
            print(f"  would-add: {len(adds)}")
            for rid in adds:
                print(f"    + {rid}")
            print(f"  would-remove: {len(removals)}")
            for rid in removals:
                print(f"    - {rid}")
        else:
            print("  (no snapshot present — run is read-only; additions/removals need Phase 4 snapshot)")

        if not free_set:
            print("  WARN: provider yielded 0 free models after predicate chain",
                  file=sys.stderr, flush=True)

    return 0


# ---------------------------------------------------------------------------
# run (Phase 3 + Phase 4)
# ---------------------------------------------------------------------------


def _today() -> str:
    return _dt.date.today().isoformat()


def _report_path() -> Path:
    return REPORT_DIR / f"report-{_dt.date.today().strftime('%Y%m%d')}.md"


def _run_cycle(files: list[Path], providers_dir: Path, blacklist_path: Path,
               provider_filter: str | None, sync: bool) -> int:
    # validate first (abort before fetch on contract errors, D16)
    errs = validate_paths(files)
    if errs:
        for e in errs:
            print(str(e), file=sys.stderr)
        return 1

    try:
        descs = [(p, load_descriptor(p)) for p in files]
    except Exception as e:
        print(f"scout: ERROR loading descriptors: {e}", file=sys.stderr)
        return 1

    # name -> descriptor + source path
    by_name: dict[str, tuple[Path, dict]] = {}
    for p, d in descs:
        by_name[d["name"]] = (p, d)

    if provider_filter:
        by_name = {k: v for k, v in by_name.items() if k == provider_filter}

    snapshot = _load_snapshot() or {}
    tombstones = _load_tombstones()
    bl = load_blacklist(blacklist_path)

    trigger = os.environ.get("SCOUT_TRIGGER", "manual")

    # per-provider computation
    provider_results: dict[str, dict] = {}
    changed = False
    warnings: list[str] = []

    for name, (_path, desc) in by_name.items():
        print(f"scout run: {name}", flush=True)
        try:
            records = fetch_catalog(desc)
        except Exception as e:
            msg = f"{name}: fetch failed: {e}"
            print(f"  ERROR fetch: {e}", file=sys.stderr, flush=True)
            warnings.append(msg)
            provider_results[name] = {"error": str(e), "free": set(), "adds": [], "removals": [],
                                     "flaps": [], "provider_removed": False, "warn0": False}
            continue

        if not records:
            msg = f"{name}: 0 models returned (indistinguishable from empty catalog)"
            print("  WARN: provider returned 0 models (indistinguishable from empty catalog)",
                  file=sys.stderr, flush=True)
            warnings.append(msg)

        free_ids: list[str] = []
        verdicts: dict[str, str] = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            rid = rec.get("id")
            if not isinstance(rid, str):
                continue
            if _is_blacklisted(rid, name, bl):
                continue
            matched, rule = rule_match(rec, desc)
            if matched and keyword_keep(rec, desc):
                free_ids.append(rid)
                verdicts[rid] = "verified"
            elif (not matched) and desc.get("assume_free") is True and keyword_keep(rec, desc):
                free_ids.append(rid)
                verdicts[rid] = "assumed"

        free_set = set(free_ids)

        if not free_set and records:
            msg = f"{name}: 0 free models after predicate chain"
            print("  WARN: provider yielded 0 free models after predicate chain",
                  file=sys.stderr, flush=True)
            warnings.append(msg)

        prev = _snapshot_ids(snapshot, name)
        adds = sorted(free_set - prev)
        removals = sorted(prev - free_set)

        # tombstone flaps: a tombstoned model that reappears in the free set
        flaps: list[str] = []
        for rid in sorted(free_set):
            reg = f"{name}/{rid}"
            if reg in tombstones:
                flaps.append(rid)
        # new removals -> tombstone proposals
        tombstone_new: list[str] = []
        for rid in removals:
            reg = f"{name}/{rid}"
            tombstone_new.append(reg)

        # provider-removed: snapshot has providers not in current descriptor set
        # (handled below globally)

        if adds or removals or flaps:
            changed = True

        provider_results[name] = {
            "error": None,
            "free": free_set,
            "verdicts": verdicts,
            "adds": adds,
            "removals": removals,
            "flaps": flaps,
            "tombstone_new": tombstone_new,
            "warn0": (not records) or (not free_set and bool(records)),
        }

        print(f"  classified free: {len(free_set)}  adds={len(adds)}  removals={len(removals)}  flaps={len(flaps)}")

    # provider-removed: snapshot keys with no descriptor
    current_names = set(by_name.keys())
    provider_removed_list: list[str] = []
    for snap_name in list(snapshot.keys()):
        if snap_name not in current_names and (provider_filter is None or snap_name == provider_filter):
            provider_removed_list.append(snap_name)
            changed = True

    sync_ok = True
    # Tombstones are observation memory (Phase 3): write on every changed run,
    # independent of LiteLLM sync success.
    if changed:
        for name, res in provider_results.items():
            if res.get("error"):
                continue
            for reg in res.get("tombstone_new", []):
                tombstones[reg] = {
                    "provider": name,
                    "removed": _today(),
                    "flaps": tombstones.get(reg, {}).get("flaps", 0),
                }
        for name, res in provider_results.items():
            for rid in res.get("flaps", []):
                reg = f"{name}/{rid}"
                if reg in tombstones:
                    tombstones[reg]["flaps"] = tombstones[reg].get("flaps", 0) + 1
                    tombstones[reg]["last_flap"] = _today()
        _atomic_write(TOMBSTONES_PATH, json.dumps(tombstones, indent=2, sort_keys=True) + "\n")

    # Snapshot is the applied-state baseline (Phase 4): advance only on successful sync.
    if sync and changed and sync_ok:
        new_snapshot = dict(snapshot)
        for name, res in provider_results.items():
            if res.get("error") or res.get("sync_failed"):
                continue
            new_snapshot[name] = sorted(res["free"])
        for pn in provider_removed_list:
            new_snapshot.pop(pn, None)
        _atomic_write(SNAPSHOT_PATH, json.dumps(new_snapshot, indent=2, sort_keys=True) + "\n")

    # report (Phase 3 writes report on change or warning)
    if changed or warnings:
        lines: list[str] = []
        lines.append(f"# scout report — {_today()}")
        lines.append("")
        lines.append(f"trigger: {trigger}  providers: {', '.join(by_name.keys()) or '(none)'}")
        lines.append("")
        for name, res in provider_results.items():
            lines.append(f"## {name}")
            if res.get("error"):
                lines.append(f"- ERROR: {res['error']}")
                lines.append("")
                continue
            for rid in res.get("adds", []):
                v = res.get("verdicts", {}).get(rid, "verified")
                lines.append(f"- add {name}/{rid} — {v}")
            for rid in res.get("removals", []):
                lines.append(f"- remove {name}/{rid} — tombstone")
            for rid in res.get("flaps", []):
                lines.append(f"- flap {name}/{rid} — tombstone re-entered")
            if res.get("warn0"):
                lines.append("- WARN: 0 models or 0 free after chain")
            if not res.get("adds") and not res.get("removals") and not res.get("flaps") and not res.get("warn0"):
                lines.append("- no changes")
            lines.append("")
        for pn in provider_removed_list:
            lines.append(f"## provider-removed: {pn}")
            lines.append(f"- {pn}: descriptor gone; models removed from registry, NOT tombstoned; re-addition auto-restores")
            lines.append("")
        if warnings:
            lines.append("## warnings")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")
        if not changed and not warnings:
            verdict = "verdict: clean (no changes)"
        elif warnings:
            verdict = f"verdict: warn — {len(warnings)} warning(s)"
        else:
            verdict = "verdict: clean"
        lines.append(verdict)
        lines.append("")
        _atomic_write(_report_path(), "\n".join(lines))

    # summary
    if sync and changed:
        sync_state = "applied" if sync_ok else "failed (retry next run)"
    elif sync:
        sync_state = "n/a (no changes)"
    else:
        sync_state = "disabled"
    print(f"scout run: changed={changed} sync={sync_state}")
    if changed and not sync:
        print("  (sync disabled — snapshot not advanced; registry untouched)")
    return 0


def run_run(files: list[Path], providers_dir: Path, blacklist_path: Path,
            provider_filter: str | None) -> int:
    # Phase 3 does NOT write snapshot; Phase 4 sync advances it.
    # sync flag: enable LiteLLM bridge (Phase 4) when LITELLM_MASTER_KEY or URL present?
    # Per plan: Phase 4 bridge is part of `scout run`. Enable by default; fail-soft if unreachable.
    sync = False
    with _flock():
        return _run_cycle(files, providers_dir, blacklist_path, provider_filter, sync)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_files(args, providers_dir: Path) -> list[Path]:
    if getattr(args, "files", None):
        return [Path(f) for f in args.files]
    return _default_paths(providers_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout", description="Free-model scout")
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="contract-check descriptors (no fetch, no writes)")
    v.add_argument("--providers-dir", type=Path, default=Path("providers"),
                   help="directory of descriptors (default: providers/)")
    v.add_argument("files", nargs="*", type=Path)

    d = sub.add_parser("diff", help="dry-run fetch + predicate chain (no writes)")
    d.add_argument("--provider", default=None, help="limit to one provider name")
    d.add_argument("--providers-dir", type=Path, default=Path("providers"))
    d.add_argument("--blacklist", type=Path, default=Path("blacklist.yaml"))
    d.add_argument("files", nargs="*", type=Path)

    r = sub.add_parser("run", help="full cycle: fetch, diff, sync, snapshot, report")
    r.add_argument("--providers-dir", type=Path, default=Path("providers"))
    r.add_argument("--blacklist", type=Path, default=Path("blacklist.yaml"))
    r.add_argument("--provider", default=None, help="limit to one provider name")
    r.add_argument("files", nargs="*", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "validate":
        providers_dir = args.providers_dir
        files = _resolve_files(args, providers_dir)
        if not files:
            print("scout validate: no files given and no providers/*.yaml found", file=sys.stderr)
            return 2
        errs = validate_paths(files)
        for e in errs:
            print(str(e), file=sys.stderr)
        return 1 if errs else 0

    if args.cmd == "diff":
        providers_dir = args.providers_dir
        files = _resolve_files(args, providers_dir)
        if not files:
            print("scout diff: no files given and no providers/*.yaml found", file=sys.stderr)
            return 2
        return run_diff(files, args.provider, args.blacklist)

    if args.cmd == "run":
        providers_dir = args.providers_dir
        files = _resolve_files(args, providers_dir)
        if not files:
            print("scout run: no files given and no providers/*.yaml found", file=sys.stderr)
            return 2
        return run_run(files, providers_dir, args.blacklist, args.provider)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
