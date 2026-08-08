"""Upstream blocklist download for the blocklist subpackage.

Split from netmgr/blocklist.py (P13). Downloads plain, gzip, or ZIP sources
into the registry.
"""
from __future__ import annotations

import gzip
import io
import os
import uuid as _uuid
import zipfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import opslog

from ._config import (
    DOMAINS_DIR,
    DOWNLOAD_TIMEOUT,
    USER_AGENT,
    BlocklistError,
    _valid_domain,
    ensure_domains_dir,
)
from .registry import (
    RegistryEntry,
    init_registry,
    load_registry,
    sha256_file,
    update_registry,
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


def download(urls: list[str], force: bool = False, name: str | None = None) -> None:
    """Download one or more upstream blocklist URLs into the registry."""
    if name and len(urls) > 1:
        raise BlocklistError("--name can only be used with a single URL")

    init_registry()
    ensure_domains_dir()
    registry: list[RegistryEntry] = load_registry()

    for url in urls:
        norm: str = normalize_url(url)

        if not force and any(normalize_url(e["url"]) == norm for e in registry):
            opslog.info("Already downloaded: %s", url)
            continue

        entry_id: str = name if name else _uuid.uuid4().hex[:6]
        filename: str = f"blocklist-{entry_id}.txt"
        filepath: str = os.path.join(DOMAINS_DIR, filename)

        opslog.info("Downloading: %s", url)
        try:
            req: Request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                if resp.status != 200:
                    opslog.error("Failed to download %s: HTTP %s", url, resp.status)
                    continue
                raw_content: bytes = resp.read()
        except Exception as e:
            opslog.error("Failed to download %s: %s", url, e)
            continue

        content: str
        if raw_content[:2] == b"PK":
            opslog.info("  ZIP detected, extracting...")
            try:
                content = extract_zip(raw_content)
            except zipfile.BadZipFile:
                opslog.error("Failed to extract %s: corrupt ZIP archive", url)
                continue
        elif raw_content[:2] == b"\x1f\x8b":
            opslog.info("  Gzip detected, decompressing...")
            try:
                content = gzip.decompress(raw_content).decode("utf-8", errors="replace")
            except Exception as e:
                opslog.error("Failed to decompress %s: %s", url, e)
                continue
        else:
            content = raw_content.decode("utf-8", errors="replace")

        sample: list[str] = content.splitlines()[:100]
        if any(line.strip().startswith("||") for line in sample):
            opslog.error("ABP format detected in %s — use a hosts/plain-domain format list instead", url)
            continue

        domain_list: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if not parts:
                continue
            candidate = parts[-1].lower().strip()
            if _valid_domain(candidate):
                domain_list.append(candidate)
        if not domain_list:
            opslog.error("No domains found in %s — not a valid blocklist", url)
            continue

        try:
            with open(filepath, "w") as f:
                f.write("\n".join(domain_list) + "\n")
        except OSError as e:
            opslog.error("Failed to save %s: %s", filepath, e)
            continue

        checksum: str = sha256_file(filepath)

        registry = [
            e for e in registry
            if not (normalize_url(e["url"]) == norm and not force)
        ]
        if force:
            registry = [e for e in registry if e["id"] != entry_id]

        registry.append(RegistryEntry(
            id=entry_id,
            url=url,
            file=filename,
            checksum=checksum,
        ))

        update_registry(registry)

        preview: list[str] = domain_list[:10]
        opslog.info("  Saved: %s (%d domains, sha256:%s…)", filename, len(domain_list), checksum[:16])
        opslog.info("  Preview: %s%s", ", ".join(preview), "…" if len(domain_list) > 10 else "")
