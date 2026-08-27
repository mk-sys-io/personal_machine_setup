#!/usr/bin/env python3
"""Generate ~/.local/share/bookmarks.html for the rofi browse menu.

Reads the Ark locked-mode domain lists (base.txt + session.txt) and emits an
HTML bookmark file consumed by dotfiles/sway/scripts/browse.

Run as part of install.sh (which has sudo) so the root-owned locked files
under /opt/ark/domains/locked/ are readable. The browse script itself runs as
the user and only reads the generated HTML.
"""

from __future__ import annotations

import sys
from pathlib import Path

ARK_DATA = Path("/opt/ark")
LOCKED_DIR = ARK_DATA / "domains" / "locked"
OUTPUT = Path.home() / ".local" / "share" / "bookmarks.html"


def _read_domains() -> list[str]:
    domains: list[str] = []
    for src_name in ("base.txt", "session.txt"):
        src = LOCKED_DIR / src_name
        if not src.is_file():
            continue
        for line in src.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            domains.append(line.lstrip("*."))
    return domains


def _generate_html(domains: list[str]) -> str:
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]
    for domain in domains:
        name = domain.split(".")[0].capitalize()
        lines.append(f'    <DT><A HREF="https://{domain}">{name}</A>')
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


def main() -> int:
    domains = _read_domains()
    if not domains:
        print(f"error: no domains found under {LOCKED_DIR}", file=sys.stderr)
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(_generate_html(domains))
    print(f"wrote {OUTPUT} ({len(domains)} bookmarks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
