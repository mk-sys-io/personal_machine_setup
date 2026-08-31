#!/usr/bin/env python3
"""ask — answer questions using SearXNG context + an LLM.

Queries the local SearXNG instance (127.0.0.1:8888) for JSON results, feeds
the top-N {title,url,snippet} into an LLM chat-completions API, and prints
the answer. Also provides `ask serve`, a small HTTP server on 127.0.0.1:8787
that backs the `ai:`/`deep:`/`learn:` search engines in LibreWolf.

The LLM provider is pluggable (default: openrouter). Uses the shared
provider_registry module for provider resolution, credential lookup, and
protocol-aware chat requests (OpenAI + Gemini). No external binary is
required; everything is Python stdlib.

Usage:
  ask "q"                 quick answer (default)
  ask -r deep "q"         comprehensive report
  ask -r learn "q"        hints + links, never the answer
  ask -l "q"              AI off, plain links only
  ask -q "..." -w site    include a specific site in the search
  ask --provider gemini "q"  use a specific provider
  ask serve               HTTP server on 127.0.0.1:8787
                         (/quick, /deep, /learn path-based role routing)

Provider config:
  provider-registry add <provider>  add API key to vault.json
  <PROVIDER>_MODEL    model override env var (optional)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from provider_registry import chat_complete, get_provider, list_providers
from provider_registry.errors import ToolError

SEARXNG_URL = "http://127.0.0.1:8888"
HOST = "127.0.0.1"
PORT = 8787
TOP_N = 5
DEFAULT_PROVIDER = "openrouter"
CHAT_TIMEOUT = 120
SEARXNG_INSTALL_MARKER = os.path.expanduser("~/searxng/venv/bin/python")


@dataclass(frozen=True)
class Role:
    """A system prompt + sampling temperature for a given answer style."""

    description: str
    temperature: float
    system_prompt: str


ROLES: dict[str, Role] = {
    "quick": Role(
        "concise answer, cite sources inline, no fluff",
        0.3,
        "You are a concise research assistant. Answer the user's question "
        "directly and accurately, drawing primarily on the provided search "
        "results. Cite sources inline using bracketed numbers matching the "
        "numbered search results. Keep the answer short and to the point — "
        "no fluff, no preamble, no filler. If the search results are "
        "insufficient to answer confidently, say so plainly rather than "
        "guessing.",
    ),
    "deep": Role(
        "comprehensive markdown report, headings, numbered sources",
        0.4,
        "You are a thorough research analyst. Write a comprehensive markdown "
        "report answering the user's question, drawing on the provided search "
        "results. Use clear headings and subheadings to structure the report. "
        "Cite sources with numbered references matching the search results, "
        "and include a numbered \"Sources\" section at the end. Cover the key "
        "aspects, trade-offs, and any notable caveats. Be detailed but avoid "
        "irrelevant tangents.",
    ),
    "learn": Role(
        "never state the answer; give hints, guiding questions, links",
        0.6,
        "You are a Socratic tutor. NEVER state the answer directly. Instead, "
        "guide the user toward understanding by giving hints, asking guiding "
        "questions, and pointing to relevant links from the provided search "
        "results. Break the topic into small steps. Encourage the user to "
        "reason through each step themselves. Your goal is to help the user "
        "learn, not to hand them the answer.",
    ),
}


class AskError(Exception):
    """Raised for user-facing failures."""


def _resolve_provider(provider_id: str | None) -> str:
    """Resolve the provider id (flag > ASK_PROVIDER > default)."""
    return provider_id or os.environ.get("ASK_PROVIDER") or DEFAULT_PROVIDER


def _searxng_installed() -> bool:
    """True if SearXNG is installed (systemd user unit, else install dir).

    Prefers the systemd user unit as the authoritative "can I start it"
    signal; falls back to the install-dir marker when systemctl --user is
    unavailable (no user session, e.g. cron/non-login shell).
    """
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "list-unit-files", "searxng.service"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            return "searxng.service" in proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return os.path.exists(SEARXNG_INSTALL_MARKER)


def _searxng_query(query: str, site: str | None = None) -> list[dict[str, str]]:
    """Query SearXNG JSON and return top-N {title,url,snippet} results."""
    params = {"q": query, "format": "json"}
    if site:
        params["q"] = f"{query} site:{site}"
    url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            if not _searxng_installed():
                raise AskError(
                    "SearXNG is not installed. Install it with: "
                    "bash lib/25-searxng.sh"
                ) from exc
            raise AskError(
                "SearXNG is installed but not running. Start it with: "
                "systemctl --user start searxng"
            ) from exc
        raise AskError(
            f"failed to query SearXNG at {SEARXNG_URL}: {exc.reason}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface any other failure
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
    """Render top results as a compact context block for the LLM."""
    lines = ["Search results (from SearXNG):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


def _chat_complete(provider_id: str, role: Role, question: str, context: str) -> str:
    """Send a chat-completions request to the provider and return the answer."""
    provider = get_provider(provider_id)
    messages = [
        {"role": "system", "content": role.system_prompt},
        {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
    ]
    try:
        return chat_complete(
            provider, messages, temperature=role.temperature, timeout=CHAT_TIMEOUT
        )
    except ToolError as exc:
        raise AskError(str(exc)) from exc


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
    provider_id = _resolve_provider(args.provider)
    context = _context_block(results)
    answer = _chat_complete(provider_id, ROLES[role], args.query, context)
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
            provider_id = _resolve_provider(None)
            results = _searxng_query(query)
            context = _context_block(results)
            answer = _chat_complete(provider_id, ROLES[role], query, context)
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
        desc = ROLES[role].description
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


PROVIDER_IDS = [p.id for p in list_providers()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ask",
        description="Answer questions using SearXNG context + an LLM "
        "(provider-agnostic).",
    )
    parser.add_argument("-r", "--role", choices=sorted(ROLES), default="quick",
                        help="role: quick|deep|learn (default: quick)")
    parser.add_argument("-l", "--links-only", action="store_true",
                        help="AI off — print plain links only")
    parser.add_argument("--provider", choices=PROVIDER_IDS, default=None,
                        help="LLM provider (default: ASK_PROVIDER or openrouter)")
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
