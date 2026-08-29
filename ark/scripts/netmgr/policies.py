from __future__ import annotations

import json
from pathlib import Path

import opslog

# Gomplate-templated constants
ARK_DATA = "{{ .Env.ARK_DATA_PATH }}"

# Data-driven browser registry — adding/removing a browser = one dict entry.
# `source_template` is the staged template under ARK_DATA_PATH (deployed by
# lib/60-ark.sh). Brave/Chrome are Chromium-family: same profile layout,
# cleanup files, and bookmarks format — only the paths differ, so entries are
# built by `_chromium_browser`. `live_policy` is derived from `etc_dir`
# (Chromium convention: <etc_dir>/policies/managed/policy.json).
# `managed_bookmarks` controls whether bookmarks are generated for the browser.
# `bookmarks_format` is how they're written: "separate" (Chromium -> a
# bookmarks.json next to the policy).
# `profile_dir`/`cache_dir` are HOME-relative base dirs used by cask cleanup;
# `profile_glob` is the profile subdir pattern (Chromium uses a fixed
# "Default").
# `cleanup_files` are the per-profile data files cask removes (cache dir is
# wiped wholesale; these are the cookies/history/login files inside each
# profile).


def _chromium_browser(
    *,
    template: str,
    etc_dir: str,
    config_dir: str,
    cache_dir: str,
) -> dict[str, object]:
    """Build a Chromium-family registry entry (Brave/Chrome/Edge share the
    profile layout, cleanup files, and bookmarks format)."""
    return {
        "live_policy": f"{etc_dir}/policies/managed/policy.json",
        "source_template": f"{ARK_DATA}/{template}",
        "fix_permissions_path": etc_dir,
        "managed_bookmarks": True,
        "bookmarks_format": "separate",
        "profile_dir": config_dir,
        "cache_dir": cache_dir,
        "profile_glob": "Default",
        "cleanup_files": [
            "Cookies", "Cookies-journal",
            "History", "History-journal",
            "Login Data", "Login Data-journal",
        ],
    }


BROWSERS: dict[str, dict[str, object]] = {
    "brave": _chromium_browser(
        template="brave-policy.json.template",
        etc_dir="/etc/brave",
        config_dir=".config/BraveSoftware/Brave-Browser",
        cache_dir=".cache/BraveSoftware/Brave-Browser",
    ),
    "chrome": _chromium_browser(
        template="chrome-policy.json.template",
        etc_dir="/etc/opt/chrome",
        config_dir=".config/google-chrome",
        cache_dir=".cache/google-chrome",
    ),
}


def deploy() -> None:
    """Deploy browser policy templates and managed bookmarks."""
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
        tmp.write_text(src.read_text())
        tmp.rename(dst)
        dst.chmod(0o644)
        opslog.debug("deployed %s -> %s", src.name, dst)


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
