#!/usr/bin/env python3
"""Multi-method package installer (20-D Python port of 20-packages.sh).

Installs packages from declarative inventory files in packages/.
Each method is idempotent — skips already-installed items.

Run standalone: python3 lib/20-packages.py
Wired into install.py as step 20-packages (dispatches .py via python3).

Exit codes (verbatim from the bash original):
    0  all passed
    1  all failed
    3  partial success (mixed installed + failed)

Stdlib only: json + tarfile + dataclass + try/except (+ subprocess,
hashlib, tempfile, pathlib, urllib, re). No third-party deps.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # for load_env import-mirror (§20.1.10)

try:
    from install import load_env  # noqa: E402  (precedence mirror, never copy-paste)
except ImportError:  # pragma: no cover - repo layout broken, env-only fallback
    load_env = None  # type: ignore[assignment]

PACKAGES_DIR = Path(os.environ.get("PACKAGES_DIR", REPO_ROOT / "packages"))
LIB_DIR = Path(__file__).resolve().parent
GOPASS_CLI = LIB_DIR / "helpers" / "gopass.sh"

INSTALLED = 0
FAILED = 0


# ── env (config.txt < real env; common.sh hardcoded fallbacks) ───────────────


def _env() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if load_env is not None:
        try:
            cfg = load_env()
        except (OSError, ValueError):
            cfg = {}
    merged = dict(cfg)
    merged.update(os.environ)
    return merged


ENV = _env()


def _timeout(name: str, default: int) -> int:
    try:
        return int(ENV.get(name, default))
    except ValueError:
        return default


CURL_TIMEOUT_CONNECT = _timeout("CURL_TIMEOUT_CONNECT", 10)
CURL_TIMEOUT_API = _timeout("CURL_TIMEOUT_API", 30)
CURL_TIMEOUT_DOWNLOAD = _timeout("CURL_TIMEOUT_DOWNLOAD", 240)
CURL_TIMEOUT_INSTALL = _timeout("CURL_TIMEOUT_INSTALL", 180)


# ── log facade (message texts mirror the bash log_* calls) ───────────────────


def log(msg: object) -> None:
    print(msg)


def log_step(msg: object) -> None:
    print(f">>> {msg}")


def log_ok(msg: object) -> None:
    print(f"  OK    {msg}")


def log_warn(msg: object) -> None:
    print(f"  WARN  {msg}")


def log_error(msg: object) -> None:
    print(f"  ERROR {msg}", file=sys.stderr)


# ── process helpers ──────────────────────────────────────────────────────────


def run(cmd: list[str], **kwargs) -> tuple[int, str]:
    """Run cmd, echo captured output (log_run mirror), return (rc, output)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except OSError as exc:
        return 127, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        log(line)
    return proc.returncode, out


def run_quiet(cmd: list[str], **kwargs) -> tuple[int, str]:
    """Captured run that never raises: missing binary → (127, "").

    Mirrors bash, where a missing command under set -e inside an
    if-condition / || list is a nonzero rc, never an abort.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except OSError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def pkg_installed(name: str) -> bool:
    rc, _ = run_quiet(["dpkg", "-s", name])
    return rc == 0


def pip_installed(name: str) -> bool:
    rc, _ = run_quiet([sys.executable, "-c", f"import {name}"])
    return rc == 0


def retry(attempts: int, cmd: list[str], delay: int = 3) -> bool:
    for attempt in range(1, attempts + 1):
        rc, _ = run_quiet(cmd)
        if rc == 0:
            return True
        if attempt < attempts:
            log_warn(f"Attempt {attempt}/{attempts} failed, retrying in {delay}s...")
            time.sleep(delay)
    return False


def require_pkg_file(name: str) -> Path | None:
    path = PACKAGES_DIR / name
    if not path.is_file():
        log_warn(f"{name} not found, skipping")
        return None
    return path


def read_list(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append(raw)
    return lines


def bump_installed() -> None:
    global INSTALLED
    INSTALLED += 1


def bump_failed() -> None:
    global FAILED
    FAILED += 1


# ── GitHub API (json replaces curl|grep|head|cut; urllib replaces curl) ───────

_GITHUB_TOKEN: str | None = None
_GITHUB_TOKEN_FAILED = False


def github_token() -> str | None:
    """Fetch once per process via the gopass.sh executable CLI (12-B).

    Returns None (after logging cause + insert hint) when unavailable.
    """
    global _GITHUB_TOKEN, _GITHUB_TOKEN_FAILED
    if _GITHUB_TOKEN is not None:
        return _GITHUB_TOKEN
    if _GITHUB_TOKEN_FAILED:
        return None
    try:
        proc = subprocess.run(
            [str(GOPASS_CLI), "services/github", "key"],
            capture_output=True,
            text=True,
        )
        rc, out, err = proc.returncode, proc.stdout, (proc.stderr or "").strip()
    except OSError as exc:
        log_error(f"gopass not found — run install.py (lib/20-packages installs it): {exc}")
        _GITHUB_TOKEN_FAILED = True
        return None
    if rc != 0 or not out:
        hint = "run: gopass insert services/github key"
        log_error(f"github token unavailable (exit {rc}): {err} — {hint}")
        _GITHUB_TOKEN_FAILED = True
        return None
    _GITHUB_TOKEN = out
    return _GITHUB_TOKEN


def github_release_assets(api_url: str) -> tuple[list[dict] | None, str, str]:
    """GET a release endpoint. Returns (assets, ratelimit, body_head).

    assets is None on transport/parse failure (diagnostics in the rest).
    """
    token = github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "linux-setup-installer",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=CURL_TIMEOUT_API) as resp:
            ratelimit = resp.headers.get("X-RateLimit-Remaining", "?")
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            body = ""
        ratelimit = (exc.headers or {}).get("X-RateLimit-Remaining", "?")
        return None, ratelimit, body[:200]
    except (OSError, ValueError) as exc:
        return None, "?", str(exc)[:200]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, ratelimit, f"unparseable JSON: {exc}"[:200]
    assets = data.get("assets", []) if isinstance(data, dict) else []
    return assets, ratelimit, body[:200]


def find_asset_url(assets: list[dict], pattern: str) -> str:
    """First browser_download_url matching pattern (grep-regex semantics)."""
    try:
        rx = re.compile(pattern)
    except re.error:
        return ""
    for asset in assets:
        url = str(asset.get("browser_download_url", ""))
        if url and rx.search(url):
            return url
    return ""


def api_url_for(repo: str, version: str) -> str:
    if version and version != "latest":
        return f"https://api.github.com/repos/{repo}/releases/tags/v{version}"
    return f"https://api.github.com/repos/{repo}/releases/latest"


def resolve_asset_url(name: str, api_url: str, pattern: str) -> str:
    assets, ratelimit, body_head = github_release_assets(api_url)
    if assets is None:
        log_warn(
            f"{name}: GitHub API failed "
            f"(X-RateLimit-Remaining: {ratelimit}, body: {body_head}), skipping"
        )
        return ""
    url = find_asset_url(assets, pattern)
    if not url:
        log_warn(
            f"{name}: could not determine download URL "
            f"(X-RateLimit-Remaining: {ratelimit}), skipping"
        )
    return url


def download(url: str, dest: Path, timeout: int, retries: int = 3, delay: int = 5) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": "linux-setup-installer"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)
            return True
        except OSError as exc:
            if attempt < retries:
                log_warn(f"download attempt {attempt}/{retries} failed ({exc}), retrying...")
                time.sleep(delay)
            else:
                log(f"download failed: {exc}")
    return False


# ── inventory dataclasses (mirror the | field orders in packages/*.txt) ──────


@dataclass
class AptRepo:
    name: str
    check_cmd: str
    key_url: str
    keyring: str
    repo_line: str
    repo_file: str


@dataclass
class GithubDeb:
    name: str
    repo: str
    pattern: str
    deps: str
    version: str


@dataclass
class GithubBinary:
    name: str
    repo: str
    pattern: str
    dest: str
    version: str


@dataclass
class GithubTarball:
    name: str
    repo: str
    pattern: str
    dest: str
    version: str
    sha256: str


@dataclass
class GithubFont:
    name: str
    repo: str
    pattern: str


@dataclass
class GoInstall:
    name: str
    import_path: str
    version: str
    tags: str = ""


@dataclass
class NpmPackage:
    name: str
    check_cmd: str = ""


@dataclass
class SourceBuild:
    name: str
    repo: str
    bin: str
    version: str


@dataclass
class CargoCrate:
    name: str
    version: str


@dataclass
class CurlScript:
    name: str
    check_cmd: str
    url: str
    shell: str


def _split(line: str, nfields: int) -> list[str]:
    parts = line.split("|")
    parts += [""] * (nfields - len(parts))
    return parts[:nfields]


# ── bootstrap prerequisites ──────────────────────────────────────────────────


def bootstrap() -> None:
    if pkg_installed("curl") and pkg_installed("gnupg"):
        log_ok("curl + gnupg already installed")
        return
    log("Bootstrapping curl + gnupg...")
    run(["sudo", "apt-get", "update", "-qq"])
    run(["sudo", "apt-get", "install", "-y", "-qq", "curl", "gnupg"])
    log_ok("curl + gnupg bootstrapped")


# ── 1. APT list ──────────────────────────────────────────────────────────────


def install_apt_list() -> None:
    file = require_pkg_file("apt.txt")
    if file is None:
        return
    log_step("APT packages")
    rc, _ = run(["sudo", "apt-get", "update", "-qq"])
    if rc != 0:
        log_warn("apt-get update had errors — some packages may fail to install")
    else:
        log_ok("apt-get update clean")
    total = already = installed = failed = 0
    for line in read_list(file):
        total += 1
        if pkg_installed(line):
            log_ok(f"{line} already installed")
            already += 1
            continue
        rc, _ = run(["sudo", "apt-get", "install", "-y", "-qq", line])
        if rc == 0:
            log_ok(line)
            installed += 1
        else:
            log_error(f"{line} failed to install")
            failed += 1
    log(f"APT: {total} total, {already} existing, {installed} installed, {failed} failed")
    global INSTALLED, FAILED
    INSTALLED += installed
    FAILED += failed


# ── 2. APT repos ─────────────────────────────────────────────────────────────


def install_apt_repos() -> None:
    file = require_pkg_file("apt_repos.txt")
    if file is None:
        return
    log_step("APT repositories")
    for line in read_list(file):
        name, check_cmd, key_url, keyring, repo_line, repo_file = _split(line, 6)
        if check_cmd and cmd_exists(check_cmd):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Adding repo: {name}...")
        ok = False
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            if download(key_url, tmp_path, CURL_TIMEOUT_API, retries=1):
                with open(tmp_path, "rb") as src:
                    try:
                        proc = subprocess.run(
                            ["sudo", "gpg", "--batch", "--yes", "--dearmor", "-o", keyring],
                            stdin=src,
                            capture_output=True,
                        )
                        gpg_rc = proc.returncode
                    except OSError:
                        gpg_rc = 127
                if gpg_rc == 0:
                    tee_rc, _ = run_quiet(["sudo", "tee", repo_file], input=repo_line + "\n")
                    if tee_rc == 0:
                        rc, _ = run(["sudo", "apt-get", "update", "-qq"])
                        ok = rc == 0
        finally:
            tmp_path.unlink(missing_ok=True)
        if ok:
            log_ok(f"{name} repo added")
            bump_installed()
        else:
            log_error(f"{name} repo setup failed")
            bump_failed()


# ── 3. GitHub debs ───────────────────────────────────────────────────────────


def _deb_installed_version(name: str) -> str:
    rc, out = run_quiet(["dpkg-query", "-W", "-f=${Version}", name])
    if rc != 0:
        return ""
    return out.strip()


def _deb_version_eq(installed_version: str, pinned: str) -> bool:
    rc, _ = run_quiet(["dpkg", "--compare-versions", installed_version, "eq", pinned])
    return rc == 0


def install_github_debs() -> None:
    file = require_pkg_file("github_deb.txt")
    if file is None:
        return
    log_step("GitHub .deb releases")
    for line in read_list(file):
        name, repo, pattern, deps, version = _split(line, 5)
        version = version or "latest"
        if pkg_installed(name):
            if version != "latest":
                installed_version = _deb_installed_version(name)
                if installed_version and _deb_version_eq(installed_version, version):
                    log_ok(f"{name} {version} already installed")
                    bump_installed()
                    continue
                if installed_version:
                    log(f"Updating {name} {installed_version} → {version} (reinstall)...")
                else:
                    log(f"Installing {name} {version} (installed version probe failed)...")
            else:
                log_ok(f"{name} already installed")
                bump_installed()
                continue
        else:
            log(f"Installing {name} {version}..." if version != "latest" else f"Installing {name}...")
        url = resolve_asset_url(name, api_url_for(repo, version), pattern)
        if not url:
            bump_failed()
            continue
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_deb = Path(tmp.name)
        try:
            if not download(url, tmp_deb, CURL_TIMEOUT_DOWNLOAD, retries=1):
                log_error(f"{name}: download failed")
                bump_failed()
                continue
            if deps:
                run(["sudo", "apt-get", "install", "-y", "-qq", *deps.split()])
            rc, _ = run(["sudo", "dpkg", "-i", str(tmp_deb)])
            if rc == 0:
                log_ok(f"{name} installed")
                bump_installed()
            else:
                log_error(f"{name} dpkg install failed")
                bump_failed()
        finally:
            tmp_deb.unlink(missing_ok=True)


# ── 4. GitHub binaries ───────────────────────────────────────────────────────


def install_github_binaries() -> None:
    file = require_pkg_file("github_binary.txt")
    if file is None:
        return
    log_step("GitHub binaries")
    for line in read_list(file):
        name, repo, pattern, dest, _version = _split(line, 5)
        if cmd_exists(name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Installing {name}...")
        url = resolve_asset_url(
            name, f"https://api.github.com/repos/{repo}/releases/latest", pattern
        )
        if not url:
            bump_failed()
            continue
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_bin = Path(tmp.name)
        try:
            if download(url, tmp_bin, CURL_TIMEOUT_DOWNLOAD):
                run(["sudo", "cp", str(tmp_bin), dest])
                run(["sudo", "chmod", "755", dest])
                log_ok(f"{name} installed to {dest}")
                bump_installed()
            else:
                log_error(f"{name}: download failed")
                bump_failed()
        finally:
            tmp_bin.unlink(missing_ok=True)


# ── 4a. GitHub tarballs (+ ventoy deploy) ─────────────────────────────────────


def deploy_ventoy(dest_dir: Path, version: str) -> bool:
    marker = Path("/opt/ventoy/.installed-version")
    try:
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == version:
            log_ok(f"ventoy {version} already deployed")
            return True
    except OSError:
        pass
    src_dir: Path | None = None
    try:
        candidates = [dest_dir, *[p for p in dest_dir.iterdir() if p.is_dir()]]
    except OSError:
        candidates = []
    for cand in candidates:
        try:
            if (cand / "Ventoy2Disk.sh").is_file():
                src_dir = cand
                break
            for sub in cand.iterdir():
                if sub.is_dir() and (sub / "Ventoy2Disk.sh").is_file():
                    src_dir = sub
                    break
        except OSError:
            continue
        if src_dir is not None:
            break
    if src_dir is None:
        log_error(f"ventoy: Ventoy2Disk.sh not found under {dest_dir}")
        return False
    run(["sudo", "mkdir", "-p", "/opt/ventoy"])
    # cp -r src/. /opt/ventoy/ (trailing-dot semantics preserved via shell-free argv)
    rc, _ = run(["sudo", "cp", "-r", f"{src_dir}/.", "/opt/ventoy/"])
    if rc != 0:
        return False
    run(["sudo", "ln", "-sf", "/opt/ventoy/Ventoy2Disk.sh", "/usr/local/bin/Ventoy2Disk.sh"])
    run(["sudo", "ln", "-sf", "/opt/ventoy/VentoyGUI.x86_64", "/usr/local/bin/VentoyGUI"])
    rc, _ = run_quiet(["sudo", "tee", str(marker)], input=version + "\n")
    if rc != 0:
        return False
    log_ok(f"ventoy {version} deployed to /opt/ventoy")
    return True


def _verify_sha256(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected.lower()


def extract_tarball(archive: Path, dest_dir: Path) -> bool:
    """tarfile r:* — auto-detects gz/xz (fixes the old -xJf xz-only)."""
    try:
        with tarfile.open(archive, mode="r:*") as tf:
            tf.extractall(dest_dir, filter="data")
        return True
    except (tarfile.TarError, OSError, ValueError) as exc:
        log(f"extraction failed: {exc}")
        return False


def install_github_tarballs() -> None:
    file = require_pkg_file("github_tarball.txt")
    if file is None:
        return
    log_step("GitHub tarballs")
    for line in read_list(file):
        name, repo, pattern, dest, version, sha256 = _split(line, 6)
        dest_dir = Path.home() / dest
        try:
            non_empty = dest_dir.is_dir() and any(dest_dir.iterdir())
        except OSError:
            non_empty = False
        if non_empty:
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Installing {name}...")
        url = resolve_asset_url(name, api_url_for(repo, version), pattern)
        if not url:
            bump_failed()
            continue
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_tar = Path(tmp.name)
        try:
            if not download(url, tmp_tar, CURL_TIMEOUT_DOWNLOAD):
                log_error(f"{name}: download failed")
                bump_failed()
                continue
            if sha256 and not _verify_sha256(tmp_tar, sha256):
                log_error(f"{name}: sha256 mismatch, discarding download")
                bump_failed()
                continue
            # User-writable dir is required — apps like Telegram self-update in place
            dest_dir.mkdir(parents=True, exist_ok=True)
            if extract_tarball(tmp_tar, dest_dir):
                log_ok(f"{name} extracted to {dest_dir}")
                if name == "ventoy":
                    if deploy_ventoy(dest_dir, version):
                        bump_installed()
                    else:
                        bump_failed()
                else:
                    bump_installed()
            else:
                log_error(f"{name}: extraction failed")
                bump_failed()
        finally:
            tmp_tar.unlink(missing_ok=True)


# ── 4b. Telegram launcher (converge-then-bootstrap, verbatim logic) ──────────


def install_telegram_launcher() -> None:
    home = Path.home()
    bin_path = home / ".local/share/TelegramDesktop/Telegram/Telegram"
    apps_dir = home / ".local/share/applications"
    desktop_file = apps_dir / "telegram-desktop.desktop"
    icon = home / ".local/share/icons/hicolor/256x256/apps/telegram-desktop.png"
    if not (bin_path.is_file() and os.access(bin_path, os.X_OK)):
        return
    if list(apps_dir.glob("org.telegram.desktop.*.desktop")):
        if desktop_file.exists() or icon.exists():
            desktop_file.unlink(missing_ok=True)
            icon.unlink(missing_ok=True)
            run_quiet(["update-desktop-database", str(apps_dir)])
            log_ok("telegram-desktop bootstrap retired — using app-managed launcher")
        else:
            log_ok("telegram-desktop launcher already managed by app")
        bump_installed()
        return
    if desktop_file.is_file():
        log_ok("telegram-desktop bootstrap launcher already installed")
        bump_installed()
        return
    log_step("Telegram desktop integration")
    icon.parent.mkdir(parents=True, exist_ok=True)
    apps_dir.mkdir(parents=True, exist_ok=True)
    if download(
        "https://raw.githubusercontent.com/telegramdesktop/tdesktop/dev/Telegram/Resources/art/icon256.png",
        icon,
        CURL_TIMEOUT_DOWNLOAD,
        retries=1,
    ):
        log_ok("Telegram icon installed")
    else:
        log_warn("Telegram icon download failed — launcher will use a generic icon")
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Name=Telegram Desktop\n"
        f"Exec={bin_path} -- %u\n"
        "Icon=telegram-desktop\n"
        "Type=Application\n"
        "Categories=Network;InstantMessaging;\n"
        "MimeType=x-scheme-handler/tg;\n"
        "StartupWMClass=TelegramDesktop\n",
        encoding="utf-8",
    )
    run_quiet(["update-desktop-database", str(apps_dir)])
    log_ok("telegram-desktop bootstrap launcher installed")
    bump_installed()


# ── 5. GitHub fonts (r:* + .ttf/.otf filter; fixes -xJf xz-only) ─────────────


def extract_fonts(archive: Path, font_dir: Path) -> bool:
    try:
        with tarfile.open(archive, mode="r:*") as tf:
            members = tf.getmembers()
    except (tarfile.TarError, OSError, ValueError) as exc:
        log(f"extraction failed: {exc}")
        return False
    font_members = [
        m for m in members if m.name.lower().endswith((".ttf", ".otf")) and m.isfile()
    ]
    try:
        with tarfile.open(archive, mode="r:*") as tf:
            if font_members:
                tf.extractall(font_dir, members=font_members, filter="data")
            else:  # fallback mirrors the bash || full-extract branch
                tf.extractall(font_dir, filter="data")
        return True
    except (tarfile.TarError, OSError, ValueError) as exc:
        log(f"extraction failed: {exc}")
        return False


def install_github_fonts() -> None:
    file = require_pkg_file("github_fonts.txt")
    if file is None:
        return
    log_step("GitHub Nerd Fonts")
    font_dir = Path.home() / ".local/share/fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    for line in read_list(file):
        name, repo, pattern = _split(line, 3)
        base = pattern.split(".", 1)[0]
        installed_glob = list(font_dir.glob(f"*{base}*.ttf")) + list(
            font_dir.glob(f"*{base}*.otf")
        )
        if installed_glob:
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Installing {name}...")
        url = resolve_asset_url(
            name, f"https://api.github.com/repos/{repo}/releases/latest", pattern
        )
        if not url:
            bump_failed()
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.xz") as tmp:
            tmp_archive = Path(tmp.name)
        try:
            if not download(url, tmp_archive, CURL_TIMEOUT_DOWNLOAD, retries=1):
                log_error(f"{name}: download failed")
                bump_failed()
                continue
            if extract_fonts(tmp_archive, font_dir):
                log_ok(f"{name} extracted to {font_dir}")
                bump_installed()
            else:
                log_error(f"{name}: extraction failed")
                bump_failed()
        finally:
            tmp_archive.unlink(missing_ok=True)
    # Always rebuild font cache — covers prior interrupted runs
    rc, _ = run(["fc-cache", "-fv", str(font_dir)])
    if rc == 0:
        log_ok("Font cache updated")
    else:
        log_warn("fc-cache failed — fonts may not be detected until cache is rebuilt")


# ── 6. Go installs ───────────────────────────────────────────────────────────


def install_go_installs() -> None:
    file = require_pkg_file("go_installs.txt")
    if file is None:
        return
    if not cmd_exists("go"):
        log_error("go not found — install golang-go first")
        bump_failed()
        return
    log_step("Go tools")
    go_bin = Path.home() / "go/bin"
    for line in read_list(file):
        name, import_path, version, tags = _split(line, 4)
        if (go_bin / name).is_file() and os.access(go_bin / name, os.X_OK):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Installing {name}...")
        env = os.environ.copy()
        cmd = ["go", "install"]
        if tags:
            env["CGO_ENABLED"] = "0"
            cmd += ["-tags", tags]
        cmd.append(f"{import_path}@{version}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
            proc_out = (proc.stdout or "") + (proc.stderr or "")
            proc_rc = proc.returncode
        except OSError as exc:
            proc_out, proc_rc = str(exc), 127
        for out_line in proc_out.splitlines():
            log(out_line)
        if proc_rc != 0:
            log_error(f"{name} failed to install")
            bump_failed()
            continue
        log_ok(f"{name} installed")
        bump_installed()
        if (go_bin / name).is_file() and not Path(f"/usr/local/bin/{name}").exists():
            run(["sudo", "cp", str(go_bin / name), f"/usr/local/bin/{name}"])
            log_ok(f"{name} copied to /usr/local/bin/{name}")


# ── 6b. NPM packages ─────────────────────────────────────────────────────────


def install_npm_packages() -> None:
    file = require_pkg_file("npm_packages.txt")
    if file is None:
        return
    log_step("NPM global packages")
    if not cmd_exists("npm"):
        log_error("npm not found — install npm first")
        bump_failed()
        return
    for line in read_list(file):
        name, check_cmd = _split(line, 2)
        check_cmd = check_cmd or name
        if "@" not in name and cmd_exists(check_cmd):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Installing {name}...")
        rc, _ = run(["sudo", "npm", "install", "-g", name])
        if rc == 0:
            log_ok(f"{name} installed")
            bump_installed()
        else:
            log_error(f"{name} failed to install")
            bump_failed()


# ── 7. Source builds (cargo / make) ──────────────────────────────────────────


def _ensure_rust() -> bool:
    cargo_bin = Path.home() / ".cargo/bin"
    if cargo_bin.is_dir():
        os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"
    if cmd_exists("rustc"):
        log_ok("Rust toolchain already installed")
        return True
    log("Installing Rust toolchain...")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        script = Path(tmp.name)
    try:
        if not download("https://sh.rustup.rs", script, CURL_TIMEOUT_INSTALL, retries=1):
            log_error("Rust toolchain installation failed")
            bump_failed()
            return False
        with open(script, "rb") as src:
            try:
                proc = subprocess.run(
                    ["sh", "-s", "--", "-y"], stdin=src, capture_output=True, text=True
                )
                rust_out = (proc.stdout or "") + (proc.stderr or "")
                rust_rc = proc.returncode
            except OSError as exc:
                rust_out, rust_rc = str(exc), 127
        for out_line in rust_out.splitlines():
            log(out_line)
        if rust_rc != 0:
            log_error("Rust toolchain installation failed")
            bump_failed()
            return False
        cargo_env = Path.home() / ".cargo/env"
        if cargo_env.is_file():  # same-process PATH only; child shells source it
            os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"
        log_ok("Rust toolchain installed")
        return True
    finally:
        script.unlink(missing_ok=True)


def _fix_numlockwl_makefile(build_dir: Path) -> None:
    """Upstream link-order fix: LDFLAGS must come after object files."""
    makefile = build_dir / "makefile"
    if not makefile.is_file():
        return
    text = makefile.read_text(encoding="utf-8")
    text = text.replace("$(CC) $(CFLAGS) $(LDFLAGS)", "$(CC) $(CFLAGS)")
    text = text.replace("$@ $^", "$@ $^ $(LDFLAGS)")
    makefile.write_text(text, encoding="utf-8")


def install_source_builds(tool: str, list_name: str) -> None:
    path = PACKAGES_DIR / list_name
    if not path.is_file():
        return  # bash passes the path directly; missing file = no-op
    if tool == "cargo":
        if not _ensure_rust():
            return
    elif tool == "make":
        if not cmd_exists("make"):
            log_error("make not found")
            bump_failed()
            return
        log_ok("make already installed")
    log_step(f"{tool} builds")
    for line in read_list(path):
        name, repo, bin_name, version = _split(line, 4)
        if cmd_exists(bin_name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Building {name} from source...")
        build_dir = Path(tempfile.mkdtemp())
        try:
            if not retry(3, ["git", "clone", f"https://github.com/{repo}", str(build_dir)]):
                log_error(f"{name}: git clone failed")
                bump_failed()
                continue
            build_ok = False
            if tool == "cargo":
                args = ["--release"]
                if version != "latest" and version:
                    args += ["-p", name]
                rc, _ = run(["cargo", "build", *args], cwd=str(build_dir))
                build_ok = rc == 0
            else:
                _fix_numlockwl_makefile(build_dir)
                rc, _ = run(["make"], cwd=str(build_dir))
                build_ok = rc == 0
            if not build_ok:
                log_error(f"{name}: {tool} build failed")
                bump_failed()
                continue
            if tool == "cargo":
                found = [
                    p
                    for p in (build_dir / "target/release").iterdir()
                    if p.is_file() and p.name == bin_name
                ]
            else:
                found = [
                    p for p in build_dir.iterdir() if p.is_file() and p.name == bin_name
                ]
            if found:
                run(["sudo", "cp", str(found[0]), f"/usr/local/bin/{bin_name}"])
                run(["sudo", "chmod", "755", f"/usr/local/bin/{bin_name}"])
                log_ok(f"{name} installed to /usr/local/bin/{bin_name}")
                bump_installed()
            else:
                log_error(f"{name}: binary not found after build")
                bump_failed()
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)


# ── 7b. Cargo crates ─────────────────────────────────────────────────────────


def install_cargo_crates() -> None:
    file = require_pkg_file("cargo_crates.txt")
    if file is None:
        return
    log_step("Cargo crate installs (crates.io)")
    os.environ["PATH"] = f"{Path.home()}/.cargo/bin:{os.environ.get('PATH', '')}"
    for line in read_list(file):
        name, version = _split(line, 2)
        if cmd_exists(name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Installing {name} {version} from crates.io...")
        rc, _ = run(["cargo", "install", "--version", version, name])
        if rc == 0:
            log_ok(f"{name} {version} installed")
            bump_installed()
        else:
            log_error(f"{name} {version} failed to install")
            bump_failed()


# ── 8. Curl scripts ──────────────────────────────────────────────────────────


def install_curl_scripts() -> None:
    file = require_pkg_file("curl_scripts.txt")
    if file is None:
        return
    log_step("Curl-script tools")
    opencode_path = ENV.get("OPENCODE_PATH", "")
    if opencode_path:
        os.environ["PATH"] = f"{opencode_path}/bin:{os.environ.get('PATH', '')}"
    for line in read_list(file):
        name, check_cmd, url, shell = _split(line, 4)
        if check_cmd and cmd_exists(check_cmd):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        if not url:
            log_error(f"{name}: URL is empty, skipping")
            bump_failed()
            continue
        if shell not in ("sh", "bash"):
            log_error(f"{name}: invalid shell '{shell}' (must be sh or bash)")
            bump_failed()
            continue
        log(f"Installing {name}...")
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_script = Path(tmp.name)
        try:
            if not download(url, tmp_script, CURL_TIMEOUT_DOWNLOAD, retries=1):
                log_error(f"{name}: download failed (possible 404)")
                bump_failed()
                continue
            rc, _ = run([shell, str(tmp_script)])
            if rc == 0:
                log_ok(f"{name} installed")
                bump_installed()
            else:
                log_error(f"{name}: install script failed")
                bump_failed()
        finally:
            tmp_script.unlink(missing_ok=True)


# ── 9. Pip packages ──────────────────────────────────────────────────────────


def install_pip_packages() -> None:
    file = require_pkg_file("pip_packages.txt")
    if file is None:
        return
    log_step("Python packages (pip)")
    if not cmd_exists("pip3"):
        log_error("pip3 not found — install python3-pip first")
        bump_failed()
        return
    for line in read_list(file):
        name = line.strip()
        if pip_installed(name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log(f"Installing {name}...")
        rc, _ = run(["sudo", "pip3", "install", "--break-system-packages", name])
        if rc == 0:
            log_ok(f"{name} installed")
            bump_installed()
        else:
            log_error(f"{name} failed to install")
            bump_failed()


# ── 10. Services (warn-not-fail, no counters — verbatim) ─────────────────────


def enable_services() -> None:
    log_step("Enabling services")
    for svc in ("NetworkManager", "nftables", "power-profiles-daemon"):
        rc, _ = run_quiet(["systemctl", "is-enabled", svc])
        if rc == 0:
            log_ok(f"{svc} already enabled")
        else:
            rc, _ = run(["sudo", "systemctl", "enable", "--now", svc])
            if rc == 0:
                log_ok(f"{svc} enabled")
            else:
                log_warn(f"{svc} enable failed")


# ── 11. Espanso (no counters — verbatim) ─────────────────────────────────────


def setup_espanso() -> None:
    espanso_bin = shutil.which("espanso") or ""
    if not espanso_bin:
        return
    log_step("Espanso capabilities")
    _, getcap_out = run_quiet(["getcap", espanso_bin])
    if "cap_dac_override" in getcap_out:
        log_ok("espanso already has cap_dac_override")
    else:
        rc, _ = run(["sudo", "setcap", "cap_dac_override+p", espanso_bin])
        if rc == 0:
            log_ok("espanso: cap_dac_override set")
        else:
            log_warn("espanso: setcap failed — evdev backend may not work")
    log_step("Espanso service")
    user_rc, _ = run_quiet(["systemctl", "--user", "is-enabled", "espanso.service"])
    if user_rc == 0:
        log_ok("espanso service already registered")
    else:
        rc, _ = run(["espanso", "service", "register"])
        if rc == 0:
            log_ok("espanso: service registered")
        else:
            log_warn("espanso: service register failed — run 'espanso service register' manually")


# ── main (bash call order verbatim) ──────────────────────────────────────────


def main(argv: list[str]) -> int:
    global INSTALLED, FAILED
    log_step("Package installation")
    install_apt_repos()
    install_apt_list()
    install_github_debs()
    setup_espanso()
    install_github_binaries()
    install_github_tarballs()
    install_telegram_launcher()
    install_github_fonts()
    install_go_installs()
    install_npm_packages()
    install_source_builds("cargo", "cargo_builds.txt")
    install_source_builds("make", "make_builds.txt")
    install_cargo_crates()
    install_curl_scripts()
    enable_services()
    install_pip_packages()
    log_step(f"Packages complete: {INSTALLED} installed, {FAILED} failed")
    if FAILED > 0 and INSTALLED > 0:
        return 3
    if FAILED > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
