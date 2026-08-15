"""dnsmasq blocklist generation for the blocklist subpackage.

Split from netmgr/blocklist.py (P13). Merges all sources into
blocklist.<format>.conf (``local=/domain/`` + ``server=/domain/#`` exceptions).
"""
from __future__ import annotations

import glob
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import opslog

from ._config import (
    BATCH_SIZE,
    CUSTOM_FILE,
    DOMAINS_DIR,
    EXCEPTIONS_FILE,
    OUTPUT_FILE,
    UPSTREAM_DIR,
    BlocklistError,
    _read_domains,
    _read_exclude,
    ensure_domains_dir,
)

if TYPE_CHECKING:
    from tqdm import tqdm


class _NullBar:
    def update(self, n: int = 1) -> None:
        pass


@contextmanager
def _progress(total: int) -> Iterator[_NullBar | tqdm]:
    """Yield a tqdm progress bar, falling back to a no-op bar if unavailable."""
    try:
        from tqdm import tqdm
    except ImportError:
        yield _NullBar()
        return
    with tqdm(
        total=total,
        unit="domains",
        desc="Generating",
        ncols=80,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    ) as pbar:
        yield pbar


def generate() -> None:
    """Merge all blocklist files into blocklist.<format>.conf (local=/domain/)."""
    import polars as pl

    ensure_domains_dir()

    upstream_files: list[str] = sorted(glob.glob(os.path.join(UPSTREAM_DIR, "*.txt")))
    has_custom: bool = os.path.isfile(CUSTOM_FILE)

    if not upstream_files and not has_custom:
        opslog.info("No blocklist files found. Nothing to generate.")
        return

    for filepath in upstream_files:
        if os.path.getsize(filepath) == 0:
            raise BlocklistError(f"{filepath} is empty. Re-run 'download'.")

    if has_custom and os.path.getsize(CUSTOM_FILE) == 0:
        raise BlocklistError(f"{CUSTOM_FILE} is empty. Add domains or remove it.")

    all_domains: list[str] = []
    for filepath in upstream_files:
        all_domains.extend(_read_domains(filepath))
    if has_custom:
        all_domains.extend(_read_domains(CUSTOM_FILE))

    exclude: set[str] = _read_exclude()

    exceptions: list[str] = []
    if os.path.isfile(EXCEPTIONS_FILE):
        exceptions = _read_domains(EXCEPTIONS_FILE)

    df: pl.DataFrame = (
        pl.DataFrame({"domain": all_domains})
        .unique(subset=["domain"])
        .sort("domain")
    )

    if exclude:
        before: int = len(df)
        df = df.filter(~pl.col("domain").is_in(list(exclude)))
        excluded: int = before - len(df)
        if excluded:
            opslog.info("  Excluded: %d domain(s) from blocklist-exclude.txt", excluded)

    series: pl.Series = df["domain"]
    total: int = len(series)

    tmp_fd: int
    tmp_path: str
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=DOMAINS_DIR, prefix=".blocklist.", suffix=".tmp"
    )
    os.close(tmp_fd)

    try:
        with open(tmp_path, "w") as f, _progress(total) as pbar:
            lines: list[str] = []
            for i in range(total):
                domain: str = series[i]
                lines.append(f"local=/{domain}/")
                if len(lines) >= BATCH_SIZE:
                    f.write("\n".join(lines) + "\n")
                    lines.clear()
                    pbar.update(BATCH_SIZE)
            if lines:
                f.write("\n".join(lines) + "\n")
                pbar.update(len(lines))

            if exceptions:
                f.write("\n# Exceptions (unblock from wildcards)\n")
                for exc in exceptions:
                    f.write(f"server=/{exc}/#\n")

        os.chown(tmp_path, 0, 0)
        os.chmod(tmp_path, 0o644)
        os.rename(tmp_path, OUTPUT_FILE)

        opslog.info("Generated: %s", OUTPUT_FILE)
        opslog.info(
            "  %d domains (local=/domain/), %d source file(s)%s",
            total,
            len(upstream_files) + (1 if has_custom else 0),
            f", {len(exceptions)} exception(s) (server=/domain/#)" if exceptions else "",
        )
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise BlocklistError(f"generate failed — {e}") from e
