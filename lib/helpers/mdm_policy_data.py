#!/usr/bin/env python3
"""Single shared validator for the v2 MDM policy data file (etc/mdm/policy/policy.json)."""

from __future__ import annotations

import json
import re
from pathlib import Path

POLICY_JSON = (
    Path(__file__).resolve().parents[2] / "etc" / "mdm" / "policy" / "policy.json"
)

REQUIRED_POLICY_KEYS = (
    "schema",
    "comment",
    "restrictions",
    "apps",
)

# D10 anti-coupling: never key on serial/IMEI. A 15-digit run is IMEI-shaped.
# serial/imei match as substrings: \bserial\b misses keys like device_serial
# ("_" is a word char); no plausible false positive in policy data.
ANTICOUPLING_RE = re.compile(r"(?i)serial|imei|\b\d{15}\b")

# Documented prose fields (the file's own explanation) may legitimately use
# the word "serial"; D10 forbids serial/IMEI *data* (keys or values).
PROSE_KEYS = frozenset({"comment", "note"})


def load_policy() -> dict:
    """Load and validate etc/mdm/policy/policy.json (fails loud on drift)."""
    if not POLICY_JSON.is_file():
        raise RuntimeError(f"policy data file missing: {POLICY_JSON}")
    try:
        policy = json.loads(POLICY_JSON.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"policy data file unreadable: {e}") from e
    missing = [k for k in REQUIRED_POLICY_KEYS if k not in policy]
    if missing:
        raise RuntimeError(f"policy data missing keys: {', '.join(missing)}")

    if policy["schema"] != 2:
        raise RuntimeError(f"policy schema must be 2, got {policy['schema']!r}")

    apps = policy["apps"]
    if not isinstance(apps, list):
        raise RuntimeError("policy apps must be a list")
    seen_pkgs: set[str] = set()
    for index, app in enumerate(apps):
        if not isinstance(app, dict):
            raise RuntimeError(f"policy apps[{index}] must be a dict")
        pkg = app.get("pkg")
        if not isinstance(pkg, str) or not pkg:
            raise RuntimeError(
                f"policy apps[{index}] must have a non-empty string 'pkg'"
            )
        action = app.get("action")
        if type(action) is not int or action not in (0, 1, 2):
            raise RuntimeError(
                f"policy apps[{index}] must have an int 'action' in (0, 1, 2)"
            )
        if pkg in seen_pkgs:
            raise RuntimeError(f"policy apps[{index}] has duplicate pkg {pkg!r}")
        seen_pkgs.add(pkg)

    restrictions = policy["restrictions"]
    if not isinstance(restrictions, str) or not re.fullmatch(
        r"no_[a-z_,]*", restrictions
    ):
        raise RuntimeError(
            f"policy restrictions must match r'no_[a-z_,]*', got {restrictions!r}"
        )
    return policy


def anti_coupling_violations(policy: dict) -> list[str]:
    """Scan policy data (dict keys + values), exempting prose fields.

    A serial/IMEI join key (D10 violation) would appear as a dict key or a
    value; free-text comment/note fields are documentation, not data.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                found.update(ANTICOUPLING_RE.findall(str(k)))
                if k not in PROSE_KEYS:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            found.update(ANTICOUPLING_RE.findall(node))

    walk(policy)
    return sorted(found)
