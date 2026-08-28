#!/usr/bin/env python3
"""ask — answer questions using SearXNG context + Groq via aichat.

Queries the local SearXNG instance (127.0.0.1:8080) for JSON results, feeds
the top-N {title,url,snippet} into aichat (which talks to Groq), and prints
the answer. Also provides `ask serve`, a small HTTP server on 127.0.0.1:8787
that backs the `ai:`/`deep:`/`learn:` search engines in LibreWolf.

Usage:
  ask "q"                 quick answer (default)
  ask -r deep "q"         comprehensive report
  ask -r learn "q"        hints + links, never the answer
  ask -l "q"              AI off, plain links only
  ask -q "..." -w site    include a specific site in the search
  ask serve               HTTP server on 127.0.0.1:8787
                         (/quick, /deep, /learn path-based role routing)

Groq key: env GROQ_API_KEY or ~/.config/aichat/config.yaml (never committed).
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEARXNG_URL = "http://127.0.0.1:8080"
HOST = "127.0.0.1"
PORT = 8787
TOP_N = 5

ROLES = {
    "quick": "quick",
    "deep": "deep",
    "learn": "learn",
}

ROLE_DESCRIPTIONS = {
    "quick": "concise answer, cite sources inline, no fluff",
    "deep": "comprehensive markdown report, headings, numbered sources",
    "learn": "never state the answer; give hints, guiding questions, links",
}


class AskError(Exception):
    """Raised for user-facing failures."""


def _searxng_query(query: str, site: str | None = None) -> list[dict[str, str]]:
    """Query SearXNG JSON and return top-N {title,url,snippet} results."""
    params = {"q": query, "format": "json"}
    if site:
        params["q"] = f"{query} site:{site}"
    url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface any network/parse failure
        raise AskError(f"failed to query SearXNG at {SEARXNG_URL}: {exc}") from exc

    results = data.get("results", [])
    top: list[dict[str, str]] = []
    for r in results:
        title = r.get("title") or ""
        link = r.get("url") or ""
        snippet = r.get("content") or ""
        if title or link:
            top.append({"title": title, "url": link, "snippet": snippet})
        if len(top) >= TOP_N:
            break
    if not top:
        raise AskError("no search results returned by SearXNG")
    return top


def _context_block(results: list[dict[str, str]]) -> str:
    """Render top results as a compact context block for aichat."""
    lines = ["Search results (from SearXNG):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


def _run_aichat(role: str, question: str, context: str) -> str:
    """Run aichat with the given role and return its stdout."""
    cmd = ["aichat", "-r", role, f"{context}\n\nQuestion: {question}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise AskError(
            "aichat not found. Install it (see packages/cargo_crates.txt) and "
            "configure a Groq key via GROQ_API_KEY or ~/.config/aichat/config.yaml."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AskError("aichat timed out") from exc
    if proc.returncode != 0:
        raise AskError(f"aichat failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def _plain_links(results: list[dict[str, str]]) -> str:
    """Render just the links (AI off)."""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['url']}")
    return "\n".join(lines)


def cmd_ask(args: argparse.Namespace) -> int:
    role = args.role or "quick"
    if role not in ROLES:
        raise AskError(f"unknown role: {role!r} (expected quick|deep|learn)")
    results = _searxng_query(args.query, args.site)
    if args.links_only:
        print(_plain_links(results))
        return 0
    context = _context_block(results)
    answer = _run_aichat(role, args.query, context)
    print(answer)
    return 0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/quick"
        role = path.lstrip("/")
        if role not in ROLES:
            self._send_error(404, f"unknown role: {role!r}")
            return
        query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
        if not query:
            self._send_error(400, "missing ?q= query parameter")
            return
        try:
            results = _searxng_query(query)
            context = _context_block(results)
            answer = _run_aichat(role, query, context)
        except AskError as exc:
            self._send_error(500, str(exc))
            return
        self._send_page(role, query, answer, results)

    def _send_error(self, code: int, message: str) -> None:
        body = f"<h1>{code}</h1><p>{html.escape(message)}</p>"
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _send_page(
        self,
        role: str,
        query: str,
        answer: str,
        results: list[dict[str, str]],
    ) -> None:
        desc = ROLE_DESCRIPTIONS.get(role, "")
        links = "".join(
            f'<li><a href="{html.escape(r["url"])}">{html.escape(r["title"])}</a></li>'
            for r in results
        )
        body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(role)} — {html.escape(query)}</title>
<style>
body {{ font-family: sans-serif; max-width: 60em; margin: 2em auto; padding: 0 1em; color: #ddd; background: #1e1e1e; }}
h1 {{ font-size: 1.4em; }} .role {{ color: #888; font-size: 0.9em; }}
pre {{ white-space: pre-wrap; background: #2a2a2a; padding: 1em; border-radius: 8px; }}
ol {{ color: #aaa; }}
</style></head><body>
<h1>{html.escape(query)}</h1>
<div class="role">{html.escape(role)} — {html.escape(desc)}</div>
<pre>{html.escape(answer)}</pre>
<h2>Sources</h2>
<ol>{links}</ol>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")


def cmd_serve(args: argparse.Namespace) -> int:
    server = ThreadingHTTPServer((HOST, PORT), _Handler)
    print(f"ask serve listening on http://{HOST}:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ask",
        description="Answer questions using SearXNG context + Groq via aichat.",
    )
    parser.add_argument("-r", "--role", choices=sorted(ROLES), default="quick",
                        help="role: quick|deep|learn (default: quick)")
    parser.add_argument("-l", "--links-only", action="store_true",
                        help="AI off — print plain links only")
    parser.add_argument("-q", "--query", help="the question to ask")
    parser.add_argument("-w", "--site", help="restrict search to a site")
    parser.add_argument("question", nargs="?", help="the question to ask")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `ask serve` — run the HTTP server.
    if argv and argv[0] == "serve":
        return cmd_serve(argparse.Namespace())

    parser = build_parser()
    args = parser.parse_args(argv)

    query = args.query or args.question
    if not query:
        parser.error("a query is required: ask \"question\"")

    try:
        return cmd_ask(args)
    except AskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
