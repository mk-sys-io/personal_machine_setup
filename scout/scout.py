#!/usr/bin/env python3
"""Scout — descriptor contract layer (Phase 1).

Validates provider descriptors against the contract in scout/providers/README.md.
`scout validate [file...]` only: no fetch, no writes, exit 0/1.

Stdlib + PyYAML. YAML is loaded with a duplicate-key-rejecting SafeLoader
(D16 guardrail: bare yaml.load() executes arbitrary objects).

Usage:
    scout validate                 # all providers/*.yaml in cwd
    scout validate a.yaml b.yaml  # explicit files
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_TOP = {"name", "base_url", "auth", "free_detection"}
ALLOWED_TOP = {
    "name", "base_url", "auth", "catalog", "free_detection",
    "capabilities", "quotas", "assume_free",
}
RULE_TYPES = {"suffix", "pricing_zero", "flag", "virtual_router"}


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


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _offending(path: Path, mark: yaml.error.Mark) -> str:
    lines = _read_lines(path)
    idx = mark.line
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


def _validate_one(path: Path) -> list[ScoutError]:
    errors: list[ScoutError] = []
    lines = _read_lines(path)
    if not lines and not path.exists():
        return [ScoutError(path=path, line=1, col=1, message="file not found")]
    text = path.read_text(encoding="utf-8") if path.exists() else ""

    # Pass 1: duplicate-key detection (raises during construction).
    try:
        yaml.load(text, Loader=DuplicateKeySafeLoader)
    except _DupKey as e:
        errors.append(_mark_err(path, e.mark, e.message))
        # Still attempt compose? Duplicate makes doc ambiguous; stop here.
        return errors
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return [_mark_err(path, mark, f"YAML parse error: {e}")]

    # Pass 2: compose to preserve marks.
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

    # Required keys.
    for req in sorted(REQUIRED_TOP):
        if req not in km:
            errors.append(_mark_err(path, root.start_mark, f"missing required key {req!r}"))

    # Unknown top-level keys.
    for k, (kn, _) in km.items():
        if k not in ALLOWED_TOP:
            errors.append(_mark_err(path, kn.start_mark, f"unknown top-level key {k!r}"))

    # Type checks for present keys.
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
                        # matcher-specific keys
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
                        # unknown keys inside rule
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
    """Return (name string, mark) for the top-level 'name' key, or (None, None)."""
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
            return v.value, k.start_mark if False else v.start_mark
    return None, None


def _cross_file_duplicates(paths: list[Path]) -> list[ScoutError]:
    errors: list[ScoutError] = []
    # group by parent directory
    by_dir: dict[Path, list[Path]] = {}
    for p in paths:
        by_dir.setdefault(p.parent, []).append(p)
    for d, files in by_dir.items():
        # also include any yaml in that dir not explicitly given
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


def _default_paths() -> list[Path]:
    found = sorted(Path("providers").glob("*.yaml")) + sorted(Path("providers").glob("*.yml"))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout", description="Free-model scout")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="contract-check descriptors (no fetch, no writes)")
    v.add_argument("files", nargs="*", type=Path, help="descriptor files (default: providers/*.yaml)")
    args = parser.parse_args(argv)

    if args.cmd == "validate":
        files = [Path(f) for f in args.files] if args.files else _default_paths()
        if not files:
            print("scout validate: no files given and no providers/*.yaml found", file=sys.stderr)
            return 2
        errs = validate_paths(files)
        for e in errs:
            print(str(e), file=sys.stderr)
        return 1 if errs else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
