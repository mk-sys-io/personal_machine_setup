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
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # for load_env import-mirror (§20.1.10)
sys.path.insert(0, str(REPO_ROOT / "lib" / "helpers"))  # for preconditions + apt_common

from apt_common import (  # noqa: E402  (sys.path anchored above, Phase 8)
    APT_COMMON_OPTS,
    APT_DPKG_OPTS,
    APT_NONINTERACTIVE_ENV,
)
from preconditions import require_user  # noqa: E402  (refuse-root guard)

try:
    from install import load_env  # noqa: E402  (precedence mirror, never copy-paste)
except ImportError:  # pragma: no cover - repo layout broken, env-only fallback
    load_env = None  # type: ignore[assignment]

PACKAGES_DIR = Path(os.environ.get("PACKAGES_DIR", REPO_ROOT / "packages"))
LIB_DIR = Path(__file__).resolve().parent

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
    print(msg, flush=True)


def log_step(msg: object) -> None:
    print(f">>> {msg}", flush=True)


def log_ok(msg: object) -> None:
    print(f"  OK    {msg}", flush=True)


def log_warn(msg: object) -> None:
    print(f"  WARN  {msg}", flush=True)


def log_error(msg: object) -> None:
    print(f"  ERROR {msg}", file=sys.stderr, flush=True)


def log_progress(cur: int, total: int, msg: object) -> None:
    """Phase/item counter in the shared STEP grammar."""
    log_step(f"[{cur}/{total}] {msg}")


# ── process helpers ──────────────────────────────────────────────────────────


def run(cmd: list[str], **kwargs) -> tuple[int, str]:
    """Run cmd, stream output live, return (rc, combined output)."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **kwargs,
        )
    except OSError as exc:
        return 127, str(exc)
    assert proc.stdout is not None
    out_lines: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        out_lines.append(line)
        log(line)
    rc = proc.wait()
    return rc, "\n".join(out_lines)


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
#
# Anonymous only: the gopass token reader was removed with gopass itself
# (gopass-removal postmortem). 60 req/h unauthenticated is enough for the
# installer's few release lookups; 50-github_setup.sh owns interactive auth.


def github_release_assets(api_url: str) -> tuple[list[dict] | None, str, str]:
    """GET a release endpoint. Returns (assets, ratelimit, body_head).

    assets is None on transport/parse failure (diagnostics in the rest).
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "linux-setup-installer",
    }
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


# ── apt transaction flags ───────────────────────────────────────────────────
#
# Hoisted to lib/helpers/apt_common.py (Phase 8) — the bash mirror lives in
# lib/helpers/common.sh. Values are byte-identical to the constants that used
# to live here; every call site below is argv-unchanged. Import-safe.


# ── bootstrap prerequisites ──────────────────────────────────────────────────


def bootstrap() -> None:
    if pkg_installed("curl"):
        log_ok("curl already installed")
        return
    log_step("Bootstrapping curl...")
    run(["sudo", *APT_NONINTERACTIVE_ENV, "apt-get", "update", "-qq", *APT_COMMON_OPTS])
    run(
        [
            "sudo",
            *APT_NONINTERACTIVE_ENV,
            "apt-get",
            "install",
            "-y",
            "-qq",
            *APT_COMMON_OPTS,
            *APT_DPKG_OPTS,
            "curl",
        ]
    )
    log_ok("curl bootstrapped")


# ── 1. APT list (batched; 20-packages is unbounded, no wall-clock) ─────────
#
# Single `apt-get install` for all missing packages: one resolver + fetch +
# dpkg cycle instead of ~148, so man-db/fontconfig/initramfs triggers fire
# once and WiFi variance is paid once, not per package. `apt-get update`
# keeps its own small retry loop -- the only bounded network call in step.


_UNAVAILABLE_RE = re.compile(r"Unable to locate package (\S+)")
_NO_CANDIDATE_RE = re.compile(r"Package (\S+) has no installation candidate")


def _apt_unavailable(names: list[str]) -> tuple[set[str], str]:
    """Return names apt cannot resolve, plus a fatal error if unattributable.

    Read-only simulation (no sudo, no lock). One unlocatable name aborts a
    whole `apt-get install`, so the batch must be filtered before the real
    transaction. A non-empty error means apt failed for a reason we could not
    pin to a name; the caller must not run the transaction.
    """
    remaining = list(names)
    unavailable: set[str] = set()
    for _ in range(len(names) + 1):
        if not remaining:
            return unavailable, ""
        rc, out = run_quiet(
            [
                *APT_NONINTERACTIVE_ENV,
                "LC_ALL=C",
                "apt-get",
                "install",
                "-s",
                "-qq",
                *APT_COMMON_OPTS,
                *remaining,
            ]
        )
        if rc == 0:
            return unavailable, ""
        found = set(_UNAVAILABLE_RE.findall(out)) | set(_NO_CANDIDATE_RE.findall(out))
        found &= set(remaining)
        if not found:
            return unavailable, out
        unavailable |= found
        remaining = [name for name in remaining if name not in found]
    return unavailable, f"apt simulation did not converge for: {' '.join(remaining)}"


def install_apt_list(skip: set[str] | None = None) -> None:
    file = require_pkg_file("apt.txt")
    if file is None:
        return
    update_ok = False
    for attempt in range(1, 4):
        rc, _ = run(
            ["sudo", *APT_NONINTERACTIVE_ENV, "apt-get", "update", "-qq", *APT_COMMON_OPTS]
        )
        if rc == 0:
            update_ok = True
            break
        log_warn(f"apt-get update attempt {attempt}/3 failed, retrying...")
        time.sleep(5)
    if not update_ok:
        log_warn("apt-get update had errors — some packages may fail to install")
    else:
        log_ok("apt-get update clean")
    entries = read_list(file)
    total = len(entries)
    already = 0
    skipped = 0
    missing: list[str] = []
    repo_skip = skip or set()
    for line in entries:
        if line in repo_skip:
            log_warn(f"{line}: skipped (repo unavailable)")
            skipped += 1
            continue
        if pkg_installed(line):
            log_ok(f"{line} already installed")
            already += 1
        else:
            missing.append(line)
    installed = failed = 0
    if missing:
        unavailable, fatal = _apt_unavailable(missing)
        for name in sorted(unavailable):
            log_warn(f"{name}: not installable from configured repos — excluding")
        if fatal:
            log_error("apt simulation failed for reasons not tied to a package name")
            for line in fatal.splitlines():
                log_error(line)
            failed = len(missing)
        else:
            missing = [name for name in missing if name not in unavailable]
            failed += len(unavailable)
            if missing:
                log_step(
                    f"Installing {len(missing)} missing packages in one apt transaction..."
                )
                rc, _ = run(
                    [
                        "sudo",
                        *APT_NONINTERACTIVE_ENV,
                        "apt-get",
                        "install",
                        "-y",
                        "-qq",
                        *APT_COMMON_OPTS,
                        *APT_DPKG_OPTS,
                        *missing,
                    ]
                )
                if rc == 0:
                    for line in missing:
                        if pkg_installed(line):
                            log_ok(line)
                            installed += 1
                        else:
                            log_error(f"{line}: not installed after apt reported success")
                            failed += 1
                else:
                    log_error("apt batch install failed")
                    failed += len(missing)
    log_step(
        f"APT: {total} total, {already} existing, {skipped} skipped, "
        f"{installed} installed, {failed} failed"
    )
    global INSTALLED, FAILED
    INSTALLED += installed
    FAILED += failed


# ── 2. APT repos ─────────────────────────────────────────────────────────────


def install_apt_repos() -> set[str]:
    """Add third-party repos; return the names whose setup failed."""
    file = require_pkg_file("apt_repos.txt")
    if file is None:
        return set()
    entries = read_list(file)
    if not cmd_exists("gpg"):
        names = {_split(line, 6)[0] for line in entries}
        log_error("gpg missing — stage-0 did not run; skipping third-party repos")
        for name in sorted(names):
            log_error(f"{name} repo setup failed: gpg missing")
            bump_failed()
        return names
    failed: set[str] = set()
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, check_cmd, key_url, keyring, repo_line, repo_file = _split(line, 6)
        if check_cmd and cmd_exists(check_cmd):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Adding repo: {name}")
        ok = False
        reason = ""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            if not download(key_url, tmp_path, CURL_TIMEOUT_API, retries=1):
                reason = "key download failed"
            else:
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
                if gpg_rc != 0:
                    reason = f"gpg --dearmor failed (rc={gpg_rc})"
                else:
                    tee_rc, _ = run_quiet(["sudo", "tee", repo_file], input=repo_line + "\n")
                    if tee_rc != 0:
                        reason = f"writing {repo_file} failed (rc={tee_rc})"
                    else:
                        rc, _ = run(
                            [
                                "sudo",
                                *APT_NONINTERACTIVE_ENV,
                                "apt-get",
                                "update",
                                "-qq",
                                *APT_COMMON_OPTS,
                            ]
                        )
                        if rc == 0:
                            ok = True
                        else:
                            reason = f"apt-get update failed (rc={rc})"
        finally:
            tmp_path.unlink(missing_ok=True)
        if ok:
            log_ok(f"{name} repo added")
            bump_installed()
        else:
            log_error(f"{name} repo setup failed: {reason or 'unknown error'}")
            bump_failed()
            failed.add(name)
    return failed


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
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, repo, pattern, _deps, version = _split(line, 5)
        version = version or "latest"
        if pkg_installed(name):
            if version != "latest":
                installed_version = _deb_installed_version(name)
                if installed_version and _deb_version_eq(installed_version, version):
                    log_ok(f"{name} {version} already installed")
                    bump_installed()
                    continue
                if installed_version:
                    log_progress(
                        idx, total, f"Updating {name} {installed_version} → {version} (reinstall)"
                    )
                else:
                    log_progress(
                        idx, total, f"Installing {name} {version} (installed version probe failed)"
                    )
            else:
                log_ok(f"{name} already installed")
                bump_installed()
                continue
        else:
            log_progress(
                idx, total, f"Installing {name} {version}" if version != "latest" else f"Installing {name}"
            )
        url = resolve_asset_url(name, api_url_for(repo, version), pattern)
        if not url:
            bump_failed()
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=".deb") as tmp:
            tmp_deb = Path(tmp.name)
        try:
            if not download(url, tmp_deb, CURL_TIMEOUT_DOWNLOAD, retries=1):
                log_error(f"{name}: download failed")
                bump_failed()
                continue
            # apt resolves Depends from the local file; the .deb suffix is
            # required for apt to accept the path form.
            rc, _ = run(
                [
                    "sudo",
                    *APT_NONINTERACTIVE_ENV,
                    "apt-get",
                    "install",
                    "-y",
                    "-qq",
                    *APT_COMMON_OPTS,
                    *APT_DPKG_OPTS,
                    str(tmp_deb),
                ]
            )
            if rc == 0:
                log_ok(f"{name} installed")
                bump_installed()
            else:
                log_error(f"{name} apt install failed")
                bump_failed()
        finally:
            tmp_deb.unlink(missing_ok=True)


# ── 4. GitHub binaries ───────────────────────────────────────────────────────


def _install_binary_artifact(name: str, url: str, src: Path, dest: str) -> bool:
    """Place a downloaded release asset at dest.

    Plain binaries are copied verbatim. Archives (.tar.gz/.tgz/.tar.xz/.zip)
    are scanned for a member named `name` and only that member's bytes are
    installed (read into memory — never extracted to disk, so no path
    traversal). Returns True on success.
    """
    lower = url.lower()
    if not lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".zip")):
        run(["sudo", "cp", str(src), dest])
        run(["sudo", "chmod", "755", dest])
        return True
    payload: bytes | None = None
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(src) as zf:
                for member in zf.namelist():
                    if member.endswith("/") or Path(member).name != name:
                        continue
                    payload = zf.read(member)
                    break
        else:
            with tarfile.open(src, mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile() or Path(member.name).name != name:
                        continue
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        payload = extracted.read()
                    break
    except (tarfile.TarError, zipfile.BadZipFile, OSError, ValueError) as exc:
        log(f"extraction failed: {exc}")
        return False
    if payload is None:
        log_error(f"{name}: no {name} binary inside archive")
        return False
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_payload = Path(tmp.name)
    try:
        tmp_payload.write_bytes(payload)
        run(["sudo", "cp", str(tmp_payload), dest])
        run(["sudo", "chmod", "755", dest])
        return True
    finally:
        tmp_payload.unlink(missing_ok=True)


def install_github_binaries() -> None:
    file = require_pkg_file("github_binary.txt")
    if file is None:
        return
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, repo, pattern, dest, _version = _split(line, 5)
        if cmd_exists(name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Installing {name}")
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
                if _install_binary_artifact(name, url, tmp_bin, dest):
                    log_ok(f"{name} installed to {dest}")
                    bump_installed()
                else:
                    log_error(f"{name} install failed")
                    bump_failed()
            else:
                log_error(f"{name}: download failed")
                bump_failed()
        finally:
            tmp_bin.unlink(missing_ok=True)


# ── 4a. GitHub tarballs (download + extract only) ────────────────────────────────


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
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
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
        log_progress(idx, total, f"Installing {name}")
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
            # User-writable dir is required — apps that self-update rewrite files in place
            dest_dir.mkdir(parents=True, exist_ok=True)
            if extract_tarball(tmp_tar, dest_dir):
                log_ok(f"{name} extracted to {dest_dir}")
                bump_installed()
            else:
                log_error(f"{name}: extraction failed")
                bump_failed()
        finally:
            tmp_tar.unlink(missing_ok=True)


# ── 4a2. Ventoy deploy (converge, not install) ─────────────────────────────────
#
# Dedicated abstraction for Ventoy's special needs: install_github_tarballs()
# only downloads/extracts (generic for every line); this step converges the
# deployed state (/opt/ventoy + symlinks + marker) from the cache on every
# run, so a wiped /opt/ventoy, broken symlink, or pin bump self-heals
# without re-download.


def ensure_ventoy_deployed() -> None:
    file = require_pkg_file("github_tarball.txt")
    if file is None:
        return
    dest = version = ""
    for line in read_list(file):
        name, _repo, _pattern, d, v, _sha = _split(line, 6)
        if name == "ventoy":
            dest, version = d, v
            break
    if not dest or not version:
        log_error("ventoy: no pinned entry in github_tarball.txt")
        bump_failed()
        return
    dest_dir = Path.home() / dest
    marker = Path("/opt/ventoy/.installed-version")
    link = Path("/usr/local/bin/Ventoy2Disk.sh")
    try:
        deployed = marker.is_file() and marker.read_text(encoding="utf-8").strip() == version
    except OSError:
        deployed = False
    try:
        linked = link.is_file() and os.access(link, os.X_OK)
    except OSError:
        linked = False
    if deployed and linked:
        log_ok(f"ventoy {version} already deployed")
        bump_installed()
        return
    log_step(f"Converging ventoy {version} deploy...")
    if deploy_ventoy(dest_dir, version):
        bump_installed()
    else:
        bump_failed()


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
    font_dir = Path.home() / ".local/share/fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, repo, pattern = _split(line, 3)
        base = pattern.split(".", 1)[0]
        installed_glob = list(font_dir.glob(f"*{base}*.ttf")) + list(
            font_dir.glob(f"*{base}*.otf")
        )
        if installed_glob:
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Installing {name}")
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
    go_bin = Path.home() / "go/bin"
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, import_path, version, tags = _split(line, 4)
        if (go_bin / name).is_file() and os.access(go_bin / name, os.X_OK):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Installing {name}")
        env = os.environ.copy()
        cmd = ["go", "install"]
        if tags:
            env["CGO_ENABLED"] = "0"
            cmd += ["-tags", tags]
        cmd.append(f"{import_path}@{version}")
        rc, _ = run(cmd, env=env)
        if rc != 0:
            log_error(f"{name} failed to install")
            bump_failed()
            continue
        log_ok(f"{name} installed")
        bump_installed()
        if (go_bin / name).is_file() and not Path(f"/usr/local/bin/{name}").exists():
            run(["sudo", "cp", str(go_bin / name), f"/usr/local/bin/{name}"])
            log_ok(f"{name} copied to /usr/local/bin/{name}")


# ── 6b. NPM packages ─────────────────────────────────────────────────────────


def ensure_npm_prefix() -> bool:
    target = str(Path.home() / ".local")
    overrides = [k for k in os.environ if k.lower() == "npm_config_prefix"]
    if overrides:
        log_warn(f"{overrides[0]}={os.environ[overrides[0]]} overrides ~/.npmrc")
    rc, out = run_quiet(["npm", "config", "get", "prefix"])
    if rc == 0 and out.strip() == target:
        log_ok(f"npm prefix already {target}")
        return True
    log_step(f"Setting npm user prefix → {target}")
    rc, _ = run(["npm", "config", "set", "prefix", target])
    if rc != 0:
        log_error("npm prefix setup failed — refusing to install globally as root")
        return False
    rc, out = run_quiet(["npm", "config", "get", "prefix"])
    if rc != 0 or out.strip() != target:
        log_error(f"npm prefix still {out.strip()!r} — an environment override is winning")
        return False
    log_ok(f"npm prefix set to {target}")
    return True


def install_npm_packages() -> None:
    file = require_pkg_file("npm_packages.txt")
    if file is None:
        return
    if not cmd_exists("npm"):
        log_error("npm not found — install npm first")
        bump_failed()
        return
    if not ensure_npm_prefix():
        bump_failed()
        return
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, _check_cmd = _split(line, 2)
        rc, _ = run_quiet(["npm", "ls", "-g", "--depth=0", name])
        if rc == 0:
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Installing {name}")
        rc, _ = run(["npm", "install", "-g", name])
        if rc == 0:
            log_ok(f"{name} installed")
            bump_installed()
        else:
            log_error(f"{name} failed to install")
            bump_failed()


# ── 7. Source builds (make) ──────────────────────────────────────────────────


def _ensure_rust() -> bool:
    cargo_bin = Path.home() / ".cargo/bin"
    if cargo_bin.is_dir():
        os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"
    if cmd_exists("rustc"):
        log_ok("Rust toolchain already installed")
        return True
    log_step("Installing Rust toolchain...")
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


def install_make_builds(list_name: str) -> None:
    path = PACKAGES_DIR / list_name
    if not path.is_file():
        return  # bash passes the path directly; missing file = no-op
    if not cmd_exists("make"):
        log_error("make not found")
        bump_failed()
        return
    log_ok("make already installed")
    entries = read_list(path)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, repo, bin_name, _version = _split(line, 4)
        if cmd_exists(bin_name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Building {name} from source")
        build_dir = Path(tempfile.mkdtemp())
        try:
            if not retry(3, ["git", "clone", f"https://github.com/{repo}", str(build_dir)]):
                log_error(f"{name}: git clone failed")
                bump_failed()
                continue
            _fix_numlockwl_makefile(build_dir)
            rc, _ = run(["make"], cwd=str(build_dir))
            if rc != 0:
                log_error(f"{name}: make build failed")
                bump_failed()
                continue
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
    if not _ensure_rust():
        return
    os.environ["PATH"] = f"{Path.home()}/.cargo/bin:{os.environ.get('PATH', '')}"
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name, version = _split(line, 2)
        if cmd_exists(name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Installing {name} {version} from crates.io")
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
    opencode_path = ENV.get("OPENCODE_PATH", "")
    if opencode_path:
        os.environ["PATH"] = f"{opencode_path}/bin:{os.environ.get('PATH', '')}"
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
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
        log_progress(idx, total, f"Installing {name}")
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
    if not cmd_exists("pip3"):
        log_error("pip3 not found — install python3-pip first")
        bump_failed()
        return
    entries = read_list(file)
    total = len(entries)
    for idx, line in enumerate(entries, 1):
        name = line.strip()
        if pip_installed(name):
            log_ok(f"{name} already installed")
            bump_installed()
            continue
        log_progress(idx, total, f"Installing {name}")
        rc, _ = run(["sudo", "pip3", "install", "--break-system-packages", name])
        if rc == 0:
            log_ok(f"{name} installed")
            bump_installed()
        else:
            log_error(f"{name} failed to install")
            bump_failed()


# ── 10. Services (warn-not-fail, no counters — verbatim) ─────────────────────


def enable_services() -> None:
    services = ("NetworkManager", "nftables", "power-profiles-daemon")
    total = len(services)
    for idx, svc in enumerate(services, 1):
        rc, _ = run_quiet(["systemctl", "is-enabled", svc])
        if rc == 0:
            log_ok(f"{svc} already enabled")
        else:
            log_progress(idx, total, f"Enabling {svc}")
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


# ── apt pin fragments (/etc/apt/preferences.d mirror) ────────────────────────


def deploy_apt_pins() -> None:
    """Deploy system/apt/preferences.d/* to /etc/apt/preferences.d/.

    Must run before ANY apt transaction in main(): apt reads preferences.d
    fresh per invocation, so the pin only needs to be on disk before the
    first `apt-get install` argv — placing it first keeps that invariant
    unconditional. Negative pins (P < 0) make apt never install the named
    packages, even as another package's Recommends (e.g. foot via sway).
    """
    global FAILED
    pins_dir = REPO_ROOT / "system" / "apt" / "preferences.d"
    if not pins_dir.is_dir():
        log_warn("apt pins: source dir not found — skipping")
        return
    live = Path("/etc/apt/preferences.d")
    rc, _ = run(["sudo", "mkdir", "-p", str(live)])
    if rc != 0:
        log_error(f"apt pins: mkdir {live} failed")
        FAILED += 1
        return
    for src in sorted(pins_dir.iterdir()):
        if not src.is_file():
            continue
        dst = live / src.name
        cmp_rc, _ = run_quiet(["sudo", "cmp", "-s", str(src), str(dst)])
        if cmp_rc == 0:
            log_ok(f"apt pin: {src.name} already up to date")
            continue
        rc, _ = run(["sudo", "install", "-m", "0644", str(src), str(dst)])
        if rc == 0:
            log_ok(f"apt pin: {src.name} deployed")
        else:
            log_error(f"apt pin: deploy {src.name} failed — excluded packages may install")
            FAILED += 1


# ── read-only preflight ──────────────────────────────────────────────────────


def check_apt() -> int:
    """Report gpg/repo state and whether every apt.txt name resolves."""
    log_step("APT preflight (read-only)")
    if cmd_exists("gpg"):
        log_ok("gpg present")
    else:
        log_warn("gpg missing — third-party repos would be skipped (run install.py stage-0)")
    repos_file = PACKAGES_DIR / "apt_repos.txt"
    if repos_file.is_file():
        for line in read_list(repos_file):
            name, check_cmd, *_ = _split(line, 6)
            state = "present" if check_cmd and cmd_exists(check_cmd) else "missing"
            log(f"repo {name}: {state}")
    apt_file = PACKAGES_DIR / "apt.txt"
    if not apt_file.is_file():
        log_error("apt.txt not found")
        return 1
    names = read_list(apt_file)
    unavailable, fatal = _apt_unavailable(names)
    for name in sorted(unavailable):
        log_warn(f"unresolvable: {name}")
    if fatal:
        log_error("apt simulation failed:")
        for line in fatal.splitlines():
            log_error(line)
        return 1
    if unavailable:
        log_error(f"{len(unavailable)} apt.txt name(s) cannot be installed")
        return 1
    log_ok(f"all {len(names)} apt.txt names resolve")
    return 0


# ── main (bash call order verbatim) ──────────────────────────────────────────


def main(argv: list[str]) -> int:
    global INSTALLED, FAILED
    if not require_user("lib/20-packages.py", "python3 lib/20-packages.py"):
        return 1
    if "--check-apt" in argv[1:]:
        return check_apt()
    log_step("Package installation")
    repos_failed: set[str] = set()

    def run_repos() -> None:
        repos_failed.update(install_apt_repos())

    def run_apt() -> None:
        install_apt_list(repos_failed)

    sections: list[tuple[str, Callable[[], None]]] = [
        ("apt pins (preferences.d)", deploy_apt_pins),
        ("APT repositories", run_repos),
        ("APT packages", run_apt),
        ("GitHub .deb releases", install_github_debs),
        ("Espanso", setup_espanso),
        ("GitHub binaries", install_github_binaries),
        ("GitHub tarballs", install_github_tarballs),
        ("Ventoy deploy", ensure_ventoy_deployed),
        ("GitHub Nerd Fonts", install_github_fonts),
        ("Go tools", install_go_installs),
        ("NPM global packages", install_npm_packages),
        ("make builds", lambda: install_make_builds("make_builds.txt")),
        ("Cargo crates", install_cargo_crates),
        ("Curl-script tools", install_curl_scripts),
        ("Enabling services", enable_services),
        ("Python packages (pip)", install_pip_packages),
    ]
    total = len(sections)
    for idx, (name, section) in enumerate(sections, 1):
        log_progress(idx, total, name)
        started = time.monotonic()
        section()
        log_progress(idx, total, f"{name} — done ({time.monotonic() - started:.0f}s)")
    log_step(f"Packages complete: {INSTALLED} installed, {FAILED} failed")
    if FAILED > 0 and INSTALLED > 0:
        return 3
    if FAILED > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
