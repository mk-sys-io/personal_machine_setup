#!/usr/bin/env python3
"""Install SearXNG from source into a user-space venv + systemd user unit.

Converted from lib/25-searxng.sh (logic-only port): user-space, no sudo,
no container, no uWSGI. The repo's services/search/ files are the source
of truth and are copied to their live destinations here.

Idempotent: guarded by venv python executability, so re-runs skip the
clone/venv/pip steps but always re-apply settings + unit + enable.

Run standalone: python3 lib/25-searxng.py

Exit codes:
    0  pass
    1  failure
Stdlib only. Prints a RESULT {json} trailer for the install.py runner.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))  # for preconditions (ADR-011)

from preconditions import require_user  # noqa: E402  (refuse-root guard)

SEARXNG_REPO = "https://github.com/searxng/searxng"
SEARCH_SRC = REPO_ROOT / "services" / "search"

CSS_START = "/* searxng-modern:start */"
CSS_END = "/* searxng-modern:end */"
CSS_FILES = ("sxng-ltr.min.css", "sxng-rtl.min.css")


def log(msg: str) -> None:
    print(f"searxng: {msg}", flush=True)


def log_step(msg: str) -> None:
    print(f">>> searxng: {msg}", flush=True)


def log_ok(msg: str) -> None:
    print(f"  OK    searxng: {msg}", flush=True)


def log_warn(msg: str) -> None:
    print(f"  WARN  searxng: {msg}", file=sys.stderr, flush=True)


def log_error(msg: str) -> None:
    print(f"  ERROR searxng: {msg}", file=sys.stderr, flush=True)


def real_home() -> Path:
    # ADR-011: require_user() makes the SUDO_USER branch unreachable
    # (root is refused at main() entry) — HOME is always the user's.
    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def run(cmd: list[str], what: str) -> None:
    log(f"{what}: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"{what} failed (rc={r.returncode})")


def deploy_file(src: Path, dst: Path) -> bool:
    """Copy src -> dst iff content differs. Returns True when changed."""
    if not src.is_file():
        raise RuntimeError(f"source missing: {src}")
    if dst.is_file() and dst.read_bytes() == src.read_bytes():
        log_ok(f"already up to date: {dst}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    log_ok(f"deployed: {dst}")
    return True


def ensure_secret_key(settings: Path) -> bool:
    """Inject server.secret_key iff missing. Returns True when changed."""
    text = settings.read_text(encoding="utf-8")
    if re.search(r"^[ \t]+secret_key:", text, re.MULTILINE):
        return False
    # SearXNG hard-refuses to start if server.secret_key is unset or still
    # the placeholder — the repo file deliberately ships no secret (never
    # commit credentials), so generate one here. Only inject when missing
    # so re-runs don't rotate the key (would invalidate sessions).
    key = secrets.token_hex(32)
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("server:"):
            lines.insert(i + 1, f"  secret_key: {key}\n")
            break
    else:
        log_warn("no 'server:' line — skipping secret inject")
        return False
    fd, tmp = tempfile.mkstemp(dir=settings.parent, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(lines))
        os.replace(tmp, settings)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    log_ok("generated server.secret_key")
    return True


def inject_css(searx_dir: Path, modern_css: Path) -> bool:
    """Marker-delimited theme inject (strip previous, re-append)."""
    if not modern_css.is_file():
        raise RuntimeError(f"source missing: {modern_css}")
    block = modern_css.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(CSS_START) + r".*?" + re.escape(CSS_END), re.DOTALL
    )
    changed = False
    theme_dir = searx_dir / "searx" / "static" / "themes" / "simple"
    for name in CSS_FILES:
        css = theme_dir / name
        if not css.is_file():
            continue
        text = css.read_text(encoding="utf-8")
        stripped = pattern.sub("", text).rstrip("\n") + "\n"
        new_text = stripped + f"\n{CSS_START}\n{block}\n{CSS_END}\n"
        if new_text != text:
            css.write_text(new_text, encoding="utf-8")
            log_ok(f"injected modern theme into {name}")
            changed = True
        else:
            log_ok(f"theme already current in {name}")
    return changed


def main(argv: list[str]) -> int:
    _ = argv
    if not require_user("lib/25-searxng.py", "python3 lib/25-searxng.py"):
        return 1
    home = real_home()
    searxng_dir = home / "searxng"
    venv_py = searxng_dir / "venv" / "bin" / "python"
    settings = searxng_dir / "searxng" / "settings.yml"
    limiter = searxng_dir / "searx" / "limiter.toml"
    unit = home / ".config" / "systemd" / "user" / "searxng.service"

    try:
        changed = False
        if venv_py.is_file() and os.access(venv_py, os.X_OK):
            log_ok("venv already present — skipping clone/venv/pip")
        else:
            log_step("cloning SearXNG source")
            run(["git", "clone", "--depth", "1", SEARXNG_REPO, str(searxng_dir)],
                "clone")
            log_step("creating Python venv")
            run([sys.executable, "-m", "venv", str(searxng_dir / "venv")],
                "venv")
            log_step("installing SearXNG into venv")
            # SearXNG has no pyproject.toml; its setup.py imports searx
            # (needs pyyaml/msgspec) at build time. With build isolation ON,
            # pip's isolated env has only setuptools, so the build fails.
            # Pre-install build deps, then build with --no-build-isolation
            # (the official SearXNG install method).
            run([str(venv_py), "-m", "pip", "install", "-U", "pip",
                 "setuptools", "wheel", "pyyaml", "msgspec",
                 "typing-extensions", "pybind11"], "pip build deps")
            run([str(venv_py), "-m", "pip", "install", "--use-pep517",
                 "--no-build-isolation", "-e", str(searxng_dir)],
                "pip install searxng")
            changed = True

        log_step("deploying settings")
        settings.parent.mkdir(parents=True, exist_ok=True)
        if deploy_file(SEARCH_SRC / "searxng-settings.yml", settings):
            changed = True
        if ensure_secret_key(settings):
            changed = True

        log_step("deploying limiter config")
        if deploy_file(SEARCH_SRC / "limiter.toml", limiter):
            changed = True

        log_step("injecting modern dark theme CSS")
        if inject_css(searxng_dir, SEARCH_SRC / "searxng-modern-dark.css"):
            changed = True

        log_step("deploying systemd user unit")
        if deploy_file(SEARCH_SRC / "searxng.service", unit):
            changed = True

        run(["systemctl", "--user", "daemon-reload"], "daemon-reload")
        run(["systemctl", "--user", "enable", "searxng"], "enable")
        # `restart` (not `enable --now`) so WhiteNoise re-indexes the static
        # tree and picks up the injected theme CSS on every run.
        run(["systemctl", "--user", "restart", "searxng"], "restart")

        log_ok("installed and enabled")
        print(f'RESULT {{"status": "OK", "changed": {str(changed).lower()}, '
              f'"message": "installed and enabled"}}', flush=True)
        return 0
    except (OSError, RuntimeError) as e:
        log_error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
