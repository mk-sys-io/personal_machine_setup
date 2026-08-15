"""Upstream blocklist download for the blocklist subpackage.

Downloads from sources.json into focused/upstream/{source_id}.txt.
Supports hosts, plain-domain, and ABP input formats.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import zipfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import opslog

from ._config import (
    DOWNLOAD_TIMEOUT,
    SOURCES_FILE,
    UPSTREAM_DIR,
    USER_AGENT,
    BlocklistError,
    _valid_domain,
    ensure_upstream_dir,
)


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication comparison."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    elif netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    return f"{scheme}://{netloc}{path}"


def extract_zip(raw_bytes: bytes) -> str:
    """Extract hosts/domain file content from a ZIP archive."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        for name in zf.namelist():
            lower = name.lower()
            if lower.endswith((".txt", ".hosts", ".conf")) or "host" in lower or "domain" in lower:
                return zf.read(name).decode("utf-8", errors="replace")
        return zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")


def _extract_source_id(url: str) -> str:
    """Extract a human-readable ID from a URL, fallback to sha256 hash.

    Examples:
        https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
        -> stevenblack-hosts

        https://blocklistproject.github.io/Lists/porn.txt
        -> blocklistproject-porn
    """
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]

    if not path_parts:
        return hashlib.sha256(url.encode()).hexdigest()[:12]

    # Drop trailing common filenames
    if path_parts[-1] in ("hosts", "domains.txt", "domains", "blocklist.txt", "blocklist"):
        path_parts = path_parts[:-1]

    if path_parts:
        # Use last two meaningful parts: owner/repo
        slug = "-".join(path_parts[-2:]).lower()
        # Sanitize: keep only alphanumeric and hyphens
        slug = re.sub(r"[^a-z0-9\-]", "", slug)
        if slug:
            return slug

    return hashlib.sha256(url.encode()).hexdigest()[:12]


_ABP_RE = re.compile(r"^\|\|([a-zA-Z0-9._\-]+)\^?$")
_HOSTS_RE = re.compile(r"^0\.0\.0\.0\s+([a-zA-Z0-9._\-]+)$")


def _parse_line(line: str) -> str | None:
    """Parse a single line from hosts, plain-domain, or ABP format.

    Returns the domain string or None if the line is not a valid domain entry.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("!") or stripped.startswith("#"):
        return None

    # ABP format: ||domain.com^
    m = _ABP_RE.match(stripped)
    if m:
        candidate = m.group(1).lower()
        if _valid_domain(candidate):
            return candidate
        return None

    # Hosts format: 0.0.0.0 domain.com
    m = _HOSTS_RE.match(stripped)
    if m:
        candidate = m.group(1).lower()
        if _valid_domain(candidate):
            return candidate
        return None

    # Plain domain: domain.com
    candidate = stripped.split()[0].lower().strip(".")
    if _valid_domain(candidate):
        return candidate

    return None


def _parse_content(content: str) -> list[str]:
    """Parse full file content, returning list of unique domains."""
    domains: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        domain = _parse_line(line)
        if domain and domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def _load_sources() -> list[dict[str, object]]:
    """Load sources.json."""
    if not os.path.isfile(SOURCES_FILE):
        raise BlocklistError(
            f"{SOURCES_FILE} not found\n"
            "  Create etc/ark/domains/focused/sources.json with default sources"
        )
    with open(SOURCES_FILE) as f:
        data = json.load(f)
    return data.get("sources", [])


def _save_sources(sources: list[dict[str, object]]) -> None:
    """Write sources.json atomically."""
    data = {"version": 1, "sources": sources}
    tmp = SOURCES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, SOURCES_FILE)


def download(
    urls: list[str] | None = None,
    *,
    force: bool = False,
    name: str | None = None,
) -> None:
    """Download upstream blocklist(s).

    If urls is provided, downloads those specific URLs (legacy mode).
    Otherwise reads from sources.json and downloads all enabled sources.
    """
    ensure_upstream_dir()

    if urls:
        # Legacy single-URL mode (used by netmgr download <url>)
        for url in urls:
            _download_single_url(url, force=force, name=name)
        return

    # sources.json mode
    sources = _load_sources()
    changed = False

    for src in sources:
        if not src.get("enabled", False):
            continue
        url = str(src.get("url", ""))
        if not url:
            continue
        source_id = str(src.get("id", _extract_source_id(url)))
        filepath = os.path.join(UPSTREAM_DIR, f"{source_id}.txt")

        if not force and os.path.isfile(filepath) and os.path.getsize(filepath) > 0:
            opslog.info("Already downloaded: %s", source_id)
            continue

        opslog.info("Downloading: %s (%s)", source_id, url)
        content = _fetch_url(url)
        if content is None:
            continue

        domains = _parse_content(content)
        if not domains:
            opslog.error("No domains found in %s -- not a valid blocklist", url)
            continue

        _save_domains(filepath, domains)
        changed = True

        preview = domains[:10]
        opslog.info(
            "  Saved: %s.txt (%d domains)", source_id, len(domains)
        )
        opslog.info(
            "  Preview: %s%s", ", ".join(preview), "..." if len(domains) > 10 else ""
        )

    if changed:
        opslog.info("Download complete. Run 'netmgr generate' to rebuild blocklist.")


def _download_single_url(url: str, *, force: bool = False, name: str | None = None) -> None:
    """Download a single URL and save to upstream/."""
    ensure_upstream_dir()

    source_id = name if name else _extract_source_id(url)
    filepath = os.path.join(UPSTREAM_DIR, f"{source_id}.txt")

    if not force and os.path.isfile(filepath) and os.path.getsize(filepath) > 0:
        opslog.info("Already downloaded: %s", source_id)
        return

    opslog.info("Downloading: %s (%s)", source_id, url)
    content = _fetch_url(url)
    if content is None:
        return

    domains = _parse_content(content)
    if not domains:
        opslog.error("No domains found in %s -- not a valid blocklist", url)
        return

    _save_domains(filepath, domains)

    preview = domains[:10]
    opslog.info("  Saved: %s.txt (%d domains)", source_id, len(domains))
    opslog.info(
        "  Preview: %s%s", ", ".join(preview), "..." if len(domains) > 10 else ""
    )


def _fetch_url(url: str) -> str | None:
    """Fetch URL content, handling gzip/ZIP. Returns None on failure."""
    try:
        req: Request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            if resp.status != 200:
                opslog.error("Failed to download %s: HTTP %s", url, resp.status)
                return None
            raw_content: bytes = resp.read()
    except Exception as e:
        opslog.error("Failed to download %s: %s", url, e)
        return None

    if raw_content[:2] == b"PK":
        opslog.info("  ZIP detected, extracting...")
        try:
            return extract_zip(raw_content)
        except zipfile.BadZipFile:
            opslog.error("Failed to extract %s: corrupt ZIP archive", url)
            return None
    elif raw_content[:2] == b"\x1f\x8b":
        opslog.info("  Gzip detected, decompressing...")
        try:
            return gzip.decompress(raw_content).decode("utf-8", errors="replace")
        except Exception as e:
            opslog.error("Failed to decompress %s: %s", url, e)
            return None

    return raw_content.decode("utf-8", errors="replace")


def _save_domains(filepath: str, domains: list[str]) -> None:
    """Write domain list to file."""
    with open(filepath, "w") as f:
        f.write("\n".join(domains) + "\n")
    checksum = hashlib.sha256(filepath.encode()).hexdigest()
    opslog.debug("  sha256:%s", checksum[:16])
