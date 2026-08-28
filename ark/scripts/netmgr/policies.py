from __future__ import annotations

import json
from pathlib import Path

import opslog

# Gomplate-templated constants
ARK_DATA = "{{ .Env.ARK_DATA_PATH }}"
CHROME_POLICY = "{{ .Env.CHROME_POLICY_PATH }}"
LIBREWOLF_POLICY = "{{ .Env.LIBREWOLF_POLICY_PATH }}"

# LibreWolf ships its hardening default policy here; we merge our custom
# delta on top at deploy time so future LibreWolf updates flow in
# automatically instead of being frozen into a hardcoded copy.
LIBREWOLF_SHIPPED = "/usr/share/librewolf/distribution/policies.json"

# Data-driven browser registry — adding/removing a browser = one dict entry.
# `source_template` is the staged template under $ARK_DATA_PATH (deployed by
# lib/60-ark.sh). Chrome's is the flat Chromium policy template. LibreWolf's
# source is our custom delta (custom.json); `merge_shipped` tells the deploy
# to merge it over the shipped hardening default at deploy time.
# `managed_bookmarks` controls whether bookmarks are generated for the browser.
# `bookmarks_format` is how they're written: "separate" (Chromium -> a
# bookmarks.json next to the policy) or "inject" (Firefox-family -> merged into
# the deployed policies.json under the "policies" key).
# `profile_dir`/`cache_dir` are HOME-relative base dirs used by cask cleanup;
# `profile_glob` is the profile subdir pattern (Chrome uses a fixed "Default",
# LibreWolf uses Firefox-style "*.default*" profile dirs).
# `cleanup_files` are the per-profile data files cask removes (cache dir is
# wiped wholesale; these are the cookies/history/login files inside each
# profile). Chromium uses its own file names, Firefox-family uses SQLite/JSON.
BROWSERS: dict[str, dict[str, object]] = {
    "chrome": {
        "live_policy": CHROME_POLICY,
        "source_template": f"{ARK_DATA}/chrome-policy.json.template",
        "fix_permissions_path": "/etc/opt/chrome",
        "managed_bookmarks": True,
        "bookmarks_format": "separate",
        "profile_dir": ".config/google-chrome",
        "cache_dir": ".cache/google-chrome",
        "profile_glob": "Default",
        "cleanup_files": [
            "Cookies", "Cookies-journal",
            "History", "History-journal",
            "Login Data", "Login Data-journal",
        ],
    },
    "librewolf": {
        "live_policy": LIBREWOLF_POLICY,
        "source_template": f"{ARK_DATA}/librewolf-custom.json",
        "merge_shipped": True,
        "fix_permissions_path": "/etc/librewolf",
        "managed_bookmarks": True,
        "bookmarks_format": "inject",
        "profile_dir": ".librewolf",
        "cache_dir": ".cache/librewolf",
        "profile_glob": "*.default*",
        "cleanup_files": [
            "cookies.sqlite",
            "places.sqlite",
            "logins.json",
            "key4.db",
            "formhistory.sqlite",
        ],
    },
}


def deploy() -> None:
    """Deploy Chrome/LibreWolf policy templates and managed bookmarks."""
    _deploy_templates()
    _fix_permissions()
    _generate_bookmarks()
    opslog.info("browser policies deployed")


# Policy targets for integrity verification — derived from the registry.
POLICY_TARGETS = [(name, str(b["live_policy"])) for name, b in BROWSERS.items()]


def verify() -> list[str]:
    """Return names of missing/corrupted policy files (empty = all valid)."""
    broken: list[str] = []
    for name, path in POLICY_TARGETS:
        try:
            json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            broken.append(name)
    return broken


def deploy_if_needed() -> None:
    """Verify policy presence; redeploy if missing/corrupted; fail-closed if
    the redeploy does not restore every target.

    Browser policies are static infra (not mode-dependent), so mode
    transitions should not redeploy them unconditionally. This gatekeeper
    redeploys only when a target is missing/corrupted, and raises if the
    redeploy leaves any target broken (deploy() silently skips missing source
    templates, so a second verify is required to avoid an infinite loop).
    """
    broken = verify()
    if not broken:
        return
    opslog.warn(
        "browser policies missing/corrupted: %s — redeploying",
        ", ".join(broken),
    )
    deploy()
    still_broken = verify()
    if still_broken:
        raise RuntimeError(
            "browser policies still broken after redeploy: "
            + ", ".join(still_broken)
        )


def _deploy_templates() -> None:
    for name, browser in BROWSERS.items():
        src = Path(str(browser["source_template"]))
        dst = Path(str(browser["live_policy"]))
        if not src.is_file():
            opslog.warn("policy template not found for %s: %s; skipping", name, src)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp")
        if browser.get("merge_shipped"):
            tmp.write_text(_merge_librewolf_policy(src))
        else:
            tmp.write_text(src.read_text())
        tmp.rename(dst)
        dst.chmod(0o644)
        opslog.debug("deployed %s -> %s", src.name, dst)


def _merge_librewolf_policy(custom: Path) -> str:
    """Merge our custom delta over LibreWolf's shipped hardening default.

    Reads the shipped default (updates with LibreWolf), overlays our custom
    `policies` keys on top (ours win on conflict), and returns the merged
    JSON. Fails closed if the shipped default is missing or invalid.
    """
    shipped = Path(LIBREWOLF_SHIPPED)
    if not shipped.is_file():
        raise RuntimeError(
            f"LibreWolf shipped policy not found at {shipped}; "
            "cannot merge custom delta"
        )
    try:
        base = json.loads(shipped.read_text())
        delta = json.loads(custom.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"invalid policy JSON during LibreWolf merge: {e}") from e

    base_policies = base.setdefault("policies", {})
    base_policies.update(delta.get("policies", {}))
    return json.dumps(base, indent=4) + "\n"


def _generate_bookmarks() -> None:
    """Generate managed bookmarks from locked/base.txt + locked/session.txt.

    Cross-mode dependency: bookmarks are generated from locked-mode files
    in ALL modes (focused + locked). These files are the productive site
    lists that define what the user CAN access.
    """
    locked_dir = Path(ARK_DATA) / "domains" / "locked"
    bookmarks: list[dict[str, str]] = []
    for src_name in ("base.txt", "session.txt"):
        src = locked_dir / src_name
        if not src.is_file():
            continue
        for line in src.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            domain = line.lstrip("*.")
            if not domain:
                continue
            name = domain.split(".")[0].capitalize()
            bookmarks.append({"name": name, "url": f"https://{domain}"})

    for _name, browser in BROWSERS.items():
        if not browser.get("managed_bookmarks"):
            continue
        if browser.get("bookmarks_format") == "inject":
            _inject_librewolf_bookmarks(bookmarks)
        else:
            _write_chromium_bookmarks(str(browser["live_policy"]), bookmarks)


def _write_chromium_bookmarks(policy_path: str, bookmarks: list[dict[str, str]]) -> None:
    """Write ManagedBookmarks to a Chromium-family bookmarks.json."""
    policy = {"ManagedBookmarks": bookmarks}
    dest = Path(policy_path).parent / "bookmarks.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy, indent=4) + "\n")
    tmp.rename(dest)
    dest.chmod(0o644)
    opslog.debug("bookmarks deployed to %s", dest)


def _inject_librewolf_bookmarks(bookmarks: list[dict[str, str]]) -> None:
    """Merge ManagedBookmarks into the deployed LibreWolf policies.json.

    LibreWolf reads ManagedBookmarks from its own policies.json (under the
    "policies" key), unlike Chromium which uses a separate bookmarks.json.
    The file is the full merged policy (shipped hardening + our additions),
    so we read it, inject the read-only ManagedBookmarks block, and write back.
    """
    dest = Path(LIBREWOLF_POLICY)
    if not dest.is_file():
        opslog.warn("librewolf policies.json not found at %s; skipping bookmarks", dest)
        return
    try:
        data = json.loads(dest.read_text())
    except json.JSONDecodeError:
        opslog.error("librewolf policies.json at %s is not valid JSON; skipping bookmarks", dest)
        return
    data.setdefault("policies", {})["ManagedBookmarks"] = bookmarks
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=4) + "\n")
    tmp.rename(dest)
    dest.chmod(0o644)
    opslog.debug("managed bookmarks injected into %s", dest)


def _fix_permissions() -> None:
    """Remove legacy kiosk_policy.json and normalize modes on the /etc policy
    trees (recursive 755/644, parity with the old generator)."""
    for _name, browser in BROWSERS.items():
        base = Path(str(browser["fix_permissions_path"]))
        legacy = base / "policies" / "managed" / "kiosk_policy.json"
        if legacy.exists():
            legacy.unlink()
            opslog.info("removed legacy %s", legacy)
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
