#!/usr/bin/env python3
"""dep_vet.py — Deterministic dependency vetting data collector.

Collects structured metric data for an OSS dependency from multiple REST APIs.
Called by the dep_vet custom tool; outputs JSON to stdout.

Usage:
    python3 dep_vet.py <input> [version]

Input formats:
    owner/repo                        e.g. psf/requests
    https://github.com/o/r            full URL
    git@github.com:o/r.git            SSH URL
    github.com/o/r/releases/...       release asset URL
    pypi:requests                     registry spec
    npm:express
    go:github.com/sirupsen/logrus
    cargo:serde
    rubygems:rails
    maven:org.apache.commons:commons-lang3
    requests                          bare name (tries pypi then npm)
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

TIMEOUT: int = 10
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")
USER_AGENT: str = "dep-vet/2.0"

OSV_ECOSYSTEMS: dict[str, str] = {
    "pypi": "PyPI",
    "npm": "npm",
    "go": "Go",
    "cargo": "crates.io",
    "rubygems": "RubyGems",
    "maven": "Maven",
}

LIBRARIES_IO_PLATFORMS: dict[str, str] = {
    "pypi": "pypi",
    "npm": "npm",
    "go": "go",
    "cargo": "cargo",
    "rubygems": "rubygems",
    "maven": "maven",
}


class ParsedInput(TypedDict):
    host: str | None
    owner: str | None
    repo: str | None
    platform: str | None
    package_name: str | None
    is_release_asset: bool
    source_url: str


class CheckData(TypedDict):
    name: str
    score: int
    reason: str


def _new_parsed(raw: str) -> ParsedInput:
    return ParsedInput(
        host=None,
        owner=None,
        repo=None,
        platform=None,
        package_name=None,
        is_release_asset=False,
        source_url=raw,
    )


def http_get(url: str, timeout: int = TIMEOUT) -> tuple[int, Any | None, str]:
    """GET request returning (status, json_or_None, raw_body)."""
    req = Request(url, headers={"User-Agent": USER_AGENT})  # type: ignore[call-overload]
    if GITHUB_TOKEN and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body: str = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body), body
            except json.JSONDecodeError:
                return resp.status, None, body
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body), body
            except json.JSONDecodeError:
                return e.code, None, body
        except Exception:  # noqa: BLE001 — fallback for HTTPError.read() failure
            return e.code, None, ""
    except (URLError, TimeoutError, OSError):
        return 0, None, ""


def http_post(url: str, data: dict[str, Any], timeout: int = TIMEOUT) -> tuple[int, Any | None]:
    """POST JSON request returning (status, json_or_None)."""
    body: bytes = json.dumps(data).encode("utf-8")
    req = Request(  # type: ignore[call-overload]
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            resp_body: str = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(resp_body)
            except json.JSONDecodeError:
                return resp.status, None
    except HTTPError as e:
        try:
            resp_body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(resp_body)
            except json.JSONDecodeError:
                return e.code, None
        except Exception:  # noqa: BLE001 — fallback for HTTPError.read() failure
            return e.code, None
    except (URLError, TimeoutError, OSError):
        return 0, None


def parse_input(raw: str) -> ParsedInput:
    """Parse input string into structured components."""
    raw = raw.strip()
    result: ParsedInput = _new_parsed(raw)

    # Check HTTP URLs first (before registry spec — "https" matches ^[a-z]+:$)
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        host: str | None = parsed.hostname
        if host in ("github.com", "gitlab.com"):
            result["host"] = host
            path_parts: list[str] = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                result["owner"] = path_parts[0]
                result["repo"] = re.sub(r"\.git$", "", path_parts[1])
            if "/releases/download/" in parsed.path or "/archive/" in parsed.path:
                result["is_release_asset"] = True
        return result

    # SSH URL
    m = re.match(r"^git@([^.]+)\.com:(.+?)(?:\.git)?$", raw)
    if m:
        result["host"] = m.group(1) + ".com"
        parts: list[str] = m.group(2).split("/")
        if len(parts) >= 2:
            result["owner"] = parts[0]
            result["repo"] = parts[1]
        return result

    # Registry spec: pypi:requests, npm:express
    m = re.match(r"^([a-z]+):(.+)$", raw)
    if m:
        result["platform"] = m.group(1)
        result["package_name"] = m.group(2)
        return result

    # owner/repo shorthand
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) == 2 and not parts[0].startswith(".") and not parts[1].startswith("."):
            result["host"] = "github.com"
            result["owner"] = parts[0]
            result["repo"] = re.sub(r"\.git$", "", parts[1])
            return result

    # Bare name: try pypi then npm
    result["package_name"] = raw
    return result


def _parse_github_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Extract host/owner/repo from a GitHub/GitLab URL."""
    if not url:
        return None, None, None
    parsed = urlparse(url)
    host: str | None = parsed.hostname
    if host is not None and host.startswith("www."):
        host = host[4:]
    if host not in ("github.com", "gitlab.com"):
        return None, None, None
    parts: list[str] = parsed.path.strip("/").split("/")
    if len(parts) >= 2:
        return host, parts[0], re.sub(r"\.git$", "", parts[1])
    return None, None, None


def resolve_from_registry(name: str, platform: str) -> tuple[str | None, str | None, str | None]:
    """Resolve repo URL from registry metadata. Returns (host, owner, repo)."""
    if platform == "go":
        if name.startswith(("github.com/", "gitlab.com/")):
            parts: list[str] = name.split("/")
            if len(parts) >= 3:
                return parts[0], parts[1], parts[2]
        return None, None, None

    if platform == "pypi":
        url = f"https://pypi.org/pypi/{quote(name, safe='')}/json"
        status, data, _ = http_get(url)
        if status == 200 and data is not None:
            info: dict[str, Any] = data.get("info", {})
            project_urls: Any = info.get("project_urls", {})
            if isinstance(project_urls, dict):
                for key in ("Source", "source", "GitHub", "github",
                            "Repository", "repository", "homepage", "Homepage"):
                    val: str | None = project_urls.get(key)
                    if val:
                        h, o, r = _parse_github_url(val)
                        if h:
                            return h, o, r
                for v in project_urls.values():
                    if v:
                        h, o, r = _parse_github_url(str(v))
                        if h:
                            return h, o, r
            home_page: str | None = info.get("home_page")
            if home_page:
                h, o, r = _parse_github_url(home_page)
                if h:
                    return h, o, r
        return None, None, None

    if platform == "npm":
        url = f"https://registry.npmjs.org/{quote(name, safe='@/')}"
        status, data, _ = http_get(url)
        if status == 200 and data is not None:
            repo_url: str | None = None
            repo_val: Any = data.get("repository")
            if isinstance(repo_val, dict):
                repo_url = repo_val.get("url")
            elif isinstance(repo_val, str):
                repo_url = repo_val
            if repo_url:
                h, o, r = _parse_github_url(repo_url)
                if h:
                    return h, o, r
        return None, None, None

    if platform == "cargo":
        url = f"https://crates.io/api/v1/crates/{quote(name, safe='')}"
        status, data, _ = http_get(url)
        if status == 200 and data is not None:
            crate: dict[str, Any] = data.get("crate", {})
            repo_url = crate.get("repository")
            if repo_url and isinstance(repo_url, str):
                h, o, r = _parse_github_url(repo_url)
                if h:
                    return h, o, r
        return None, None, None

    if platform == "rubygems":
        url = f"https://rubygems.org/api/v1/gems/{quote(name, safe='')}.json"
        status, data, _ = http_get(url)
        if status == 200 and data is not None:
            for field in ("source_code_uri", "homepage_uri"):
                val = data.get(field)
                if val and isinstance(val, str):
                    h, o, r = _parse_github_url(val)
                    if h:
                        return h, o, r
        return None, None, None

    return None, None, None


def fetch_github(owner: str, repo: str, host: str = "github.com") -> dict[str, Any]:
    """Fetch GitHub metrics."""
    if host == "gitlab.com":
        return {"available": False, "error": "GitHub API not available for GitLab repos"}

    base: str = f"https://api.github.com/repos/{owner}/{repo}"
    status, data, _ = http_get(base)
    if status == 404:
        return {"available": False, "not_found": True, "error": "not found"}
    if status == 403:
        hint = "set GITHUB_TOKEN for 5000/hr" if not GITHUB_TOKEN else "rate limit hit"
        return {"available": False, "error": f"GitHub rate limit: {hint}"}
    if status != 200 or data is None:
        return {"available": False, "error": f"GitHub API returned {status}"}

    pushed_at: str = data.get("pushed_at", "")
    days_since_push: int | None = None
    if pushed_at:
        try:
            pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            days_since_push = (datetime.now(timezone.utc) - pushed_dt).days
        except (ValueError, TypeError):
            pass

    c_status, c_data, _ = http_get(f"{base}/contributors?per_page=100&anon=1")
    contributors: int | None = len(c_data) if c_status == 200 and isinstance(c_data, list) else None

    r_status, r_data, _ = http_get(f"{base}/releases?per_page=100")
    # count capped at 100 (GitHub per_page max; no pagination via Link header)
    releases: int = len(r_data) if r_status == 200 and isinstance(r_data, list) else 0
    latest_release: str | None = None
    if r_status == 200 and isinstance(r_data, list) and r_data:
        latest_release = r_data[0].get("tag_name")

    license_obj: Any = data.get("license")
    license_id: str | None = None
    if isinstance(license_obj, dict):
        license_id = license_obj.get("spdx_id")

    return {
        "available": True,
        "stars": data.get("stargazers_count"),
        "pushed_at": pushed_at,
        "days_since_push": days_since_push,
        "archived": data.get("archived", False),
        "license": license_id,
        "contributors": contributors,
        "releases": releases,
        "latest_release": latest_release,
    }


def fetch_scorecard(owner: str, repo: str, host: str = "github.com") -> dict[str, Any]:
    """Fetch Scorecard metrics."""
    prefix = "gitlab" if host == "gitlab.com" else "github"
    url: str = f"https://api.scorecard.dev/projects/{prefix}.com/{owner}/{repo}"
    status, data, _ = http_get(url)

    if status == 404:
        return {"available": False, "error": "Scorecard 404 (repo not indexed)"}
    if status != 200 or data is None:
        return {"available": False, "error": f"Scorecard returned {status}"}

    checks: list[dict[str, Any]] = data.get("checks", [])
    check_map: dict[str, dict[str, Any]] = {}
    for c in checks:
        name_val: str | None = c.get("name")
        if name_val:
            check_map[name_val] = c

    def pick(name: str) -> CheckData | None:
        c = check_map.get(name)
        if c is None:
            return None
        return CheckData(
            name=str(c.get("name", "")),
            score=int(c.get("score", 0)),
            reason=str(c.get("reason", "")),
        )

    return {
        "available": True,
        "score": data.get("score"),
        "maintained": pick("Maintained"),
        "code_review": pick("Code-Review"),
        "ci_tests": pick("CI-Tests"),
    }


def fetch_osv(name: str, ecosystem: str, version: str | None = None) -> dict[str, Any]:
    """Fetch OSV vulnerability data."""
    osv_eco: str | None = OSV_ECOSYSTEMS.get(ecosystem)
    if not osv_eco:
        return {"available": False, "error": f"Unknown ecosystem: {ecosystem}"}

    payload: dict[str, Any] = {"package": {"name": name, "ecosystem": osv_eco}}
    if version:
        payload["version"] = version

    status, data = http_post("https://api.osv.dev/v1/query", payload)
    if status != 200 or data is None:
        return {"available": False, "error": f"OSV returned {status}"}

    vulns: list[dict[str, Any]] = data.get("vulns", [])
    return {
        "available": True,
        "vuln_ids": [v["id"] for v in vulns if isinstance(v, dict) and v.get("id")],
        "count": len(vulns),
    }


def fetch_ossinsight(owner: str, repo: str) -> dict[str, Any]:
    """Fetch OSS Insight star history + country distribution."""
    h_url: str = f"https://api.ossinsight.io/v1/repos/{owner}/{repo}/stargazers/history"
    h_status, h_data, _ = http_get(h_url)

    c_url: str = f"https://api.ossinsight.io/v1/repos/{owner}/{repo}/stargazers/countries"
    c_status, c_data, _ = http_get(c_url)

    if h_status != 200 and c_status != 200:
        return {"available": False, "error": "OSS Insight unavailable"}

    history: list[dict[str, Any]] = []
    if h_status == 200 and h_data is not None:
        h_inner: Any = h_data.get("data")
        if isinstance(h_inner, dict):
            rows: Any = h_inner.get("rows")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    try:
                        stars_val = int(row.get("stargazers", 0))
                        if stars_val > 0:
                            history.append({"date": row.get("date"), "stargazers": stars_val})
                    except (ValueError, TypeError):
                        pass

    countries: list[dict[str, Any]] = []
    if c_status == 200 and c_data is not None:
        c_inner: Any = c_data.get("data")
        if isinstance(c_inner, dict):
            c_rows: Any = c_inner.get("rows")
            if isinstance(c_rows, list):
                for row in c_rows[:5]:
                    if not isinstance(row, dict):
                        continue
                    countries.append({
                        "country_code": row.get("country_code"),
                        "stargazers": row.get("stargazers"),
                        "percentage": row.get("percentage"),
                    })

    return {"available": True, "history": history, "countries": countries}


def fetch_libraries_io(name: str, platform: str) -> dict[str, Any]:
    """Fetch Libraries.io dependents count (soft signal)."""
    lio_plat: str | None = LIBRARIES_IO_PLATFORMS.get(platform)
    if not lio_plat:
        return {"available": False, "error": f"Unsupported platform: {platform}"}

    url: str = f"https://libraries.io/api/{lio_plat}/{quote(name, safe='')}"
    status, data, _ = http_get(url)

    if status == 429:
        return {"available": False, "error": "Libraries.io rate limited (free tier)"}
    if status == 404:
        return {"available": False, "error": "Libraries.io not found"}
    if status != 200 or data is None:
        return {"available": False, "error": f"Libraries.io returned {status}"}

    return {"available": True, "dependents_count": data.get("dependents_count"), "soft": True}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print((__doc__ or "").strip())
        sys.exit(0)

    raw_input: str = sys.argv[1]
    version: str | None = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None

    parsed: ParsedInput = parse_input(raw_input)
    errors: list[str] = []

    host: str | None = parsed["host"]
    owner: str | None = parsed["owner"]
    repo: str | None = parsed["repo"]
    platform: str | None = parsed["platform"]
    package_name: str | None = parsed["package_name"]
    ecosystem: str | None = platform
    resolved_source: str | None = None
    not_found: bool = False

    if package_name and not owner:
        if platform:
            h, o, r = resolve_from_registry(package_name, platform)
            if h and o and r:
                host, owner, repo = h, o, r
            else:
                errors.append(f"Could not resolve {platform}:{package_name} to a repo")
        else:
            for try_plat in ("pypi", "npm"):
                h, o, r = resolve_from_registry(package_name, try_plat)
                if h and o and r:
                    host, owner, repo = h, o, r
                    ecosystem = try_plat
                    platform = try_plat
                    break
            if not owner:
                errors.append(f"Could not resolve '{package_name}' from any registry")

    if host and owner and repo:
        resolved_source = f"{host}/{owner}/{repo}"

    github_data: dict[str, Any] | None = None
    scorecard_data: dict[str, Any] | None = None
    osv_data: dict[str, Any] | None = None
    ossinsight_data: dict[str, Any] | None = None
    lio_data: dict[str, Any] | None = None

    if owner and repo:
        host_val: str = host if isinstance(host, str) else "github.com"
        github_data = fetch_github(owner, repo, host_val)
        if not github_data.get("available"):
            if github_data.get("not_found"):
                not_found = True
            errors.append(f"github: {github_data.get('error', 'unavailable')}")

        if not not_found:
            scorecard_data = fetch_scorecard(owner, repo, host_val)
            if not scorecard_data.get("available"):
                errors.append(f"scorecard: {scorecard_data.get('error', 'unavailable')}")

        if package_name and ecosystem:
            osv_data = fetch_osv(package_name, ecosystem, version)
            if not osv_data.get("available"):
                errors.append(f"osv: {osv_data.get('error', 'unavailable')}")

        if host_val == "github.com":
            ossinsight_data = fetch_ossinsight(owner, repo)
            if not ossinsight_data.get("available"):
                errors.append(f"ossinsight: {ossinsight_data.get('error', 'unavailable')}")

        lio_platform: str | None = platform or ecosystem
        if lio_platform and package_name:
            lio_data = fetch_libraries_io(package_name, lio_platform)
            if not lio_data.get("available"):
                errors.append(f"libraries_io: {lio_data.get('error', 'unavailable')}")

    elif not resolved_source and not not_found:
        errors.append("No repo resolved from input")

    metrics: dict[str, Any] = {}

    if github_data is not None and github_data.get("available"):
        metrics["activity"] = {
            "pushed_at": github_data["pushed_at"],
            "days_since_push": github_data["days_since_push"],
            "archived": github_data["archived"],
            "license": github_data["license"],
        }
        metrics["community"] = {"contributors": github_data["contributors"]}
        metrics["maturity"] = {
            "releases": github_data["releases"],
            "latest_release": github_data["latest_release"],
        }

    if scorecard_data is not None and scorecard_data.get("available"):
        maintained: CheckData | None = scorecard_data.get("maintained")
        code_review: CheckData | None = scorecard_data.get("code_review")
        metrics["code_quality"] = {
            "scorecard_score": scorecard_data.get("score"),
            "maintained": maintained,
            "code_review": code_review,
        }
        if "activity" not in metrics:
            metrics["activity"] = {}
        if isinstance(maintained, dict):
            metrics["activity"]["maintained_score"] = maintained.get("score")
            metrics["activity"]["maintained_reason"] = maintained.get("reason")
        else:
            metrics["activity"]["maintained_score"] = None

    if osv_data is not None and osv_data.get("available"):
        metrics["security"] = {
            "osv_count": osv_data["count"],
            "osv_vulns": osv_data["vuln_ids"],
        }

    if ossinsight_data is not None and ossinsight_data.get("available"):
        metrics["vibe_code"] = {
            "history": ossinsight_data["history"],
            "countries": ossinsight_data["countries"],
        }

    if lio_data is not None and lio_data.get("available"):
        metrics["libraries_io"] = lio_data

    result: dict[str, Any] = {
        "input": raw_input,
        "resolved_source": resolved_source,
        "not_found": not_found,
        "platform": platform,
        "ecosystem": ecosystem,
        "version": version,
        "metrics": metrics,
        "errors": errors,
        "total_failure": (not resolved_source and bool(errors)) or not_found,
    }

    print(json.dumps(result, indent=2, default=str))
    sys.exit(0)


if __name__ == "__main__":
    main()
