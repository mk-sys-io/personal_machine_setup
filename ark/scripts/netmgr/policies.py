from __future__ import annotations

import json
from pathlib import Path

import opslog

# Gomplate-templated constants
ARK_DATA = "{{ .Env.ARK_DATA_PATH }}"
BRAVE_POLICY = "{{ .Env.BRAVE_POLICY_PATH }}"
FIREFOX_POLICY = "{{ .Env.FIREFOX_POLICY_PATH }}"
CHROMIUM_POLICY = "{{ .Env.CHROMIUM_POLICY_PATH }}"
CHROME_POLICY = "{{ .Env.CHROME_POLICY_PATH }}"


def deploy() -> None:
    """Deploy Brave/Firefox policy templates and managed bookmarks."""
    _deploy_templates()
    _fix_permissions()
    _generate_bookmarks()
    opslog.info("browser policies deployed")


def _deploy_templates() -> None:
    data = Path(ARK_DATA)
    targets = [
        (data / "brave-policy.json.template", BRAVE_POLICY),
        (data / "firefox-policies.json.template", FIREFOX_POLICY),
        (data / "brave-policy.json.template", CHROMIUM_POLICY),
        (data / "brave-policy.json.template", CHROME_POLICY),
    ]
    for src, dst in targets:
        if not src.is_file():
            continue
        dest = Path(dst)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(src.read_text())
        tmp.rename(dest)
        dest.chmod(0o644)
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

    policy = {"ManagedBookmarks": bookmarks}
    for policy_path in (BRAVE_POLICY, CHROMIUM_POLICY, CHROME_POLICY):
        dest = Path(policy_path).parent / "bookmarks.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(policy, indent=4) + "\n")
        tmp.rename(dest)
        dest.chmod(0o644)
        opslog.debug("bookmarks deployed to %s", dest)


def _fix_permissions() -> None:
    """Remove legacy kiosk_policy.json and normalize modes on the /etc policy
    trees (recursive 755/644, parity with the old generator). Fix #3: these
    trees are /etc/{brave,firefox,chromium,opt/chrome}, NOT $ARK_DATA_PATH."""
    for browser_dir in ("brave", "firefox", "chromium", "opt/chrome"):
        base = Path("/etc") / browser_dir
        legacy = base / "policies" / "managed" / "kiosk_policy.json"
        if legacy.exists():
            legacy.unlink()
            opslog.info("removed legacy %s", legacy)
    for base in (
        Path("/etc/brave"),
        Path("/etc/firefox"),
        Path("/etc/chromium"),
        Path("/etc/opt/chrome"),
    ):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
