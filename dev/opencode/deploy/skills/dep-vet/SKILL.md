---
name: dep-vet
description: Vets third-party OSS dependencies before use. Checks activity, security, quality, maturity, community, and vibe-code signals. Produces a structured verdict.
license: MIT
compatibility: opencode, claude-code
metadata:
  version: "2.0.0"
  domain: security
  role: specialist
---

# Dependency Vetting

Vets an OSS dependency across 6 metrics before the agent recommends it.

## How to Run

Call the `dep_vet` tool with the dependency spec. It returns structured JSON with per-metric data and availability flags.

**Input formats:**
- `owner/repo` — e.g. `psf/requests`
- GitHub/GitLab URL — e.g. `https://github.com/psf/requests`
- Release-asset URL — e.g. `https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz`
- Registry spec — `pypi:requests`, `npm:express`, `go:github.com/sirupsen/logrus`, `cargo:serde`, `rubygems:rails`, `maven:org.apache.commons:commons-lang3`
- Bare name — `requests` (tries pypi then npm)

**Optional:** pass a `version` arg for pinned OSV queries.

## Metrics

| Metric | Source | Threshold |
|---|---|---|
| Activity | `metrics.activity.days_since_push`; `metrics.code_quality.maintained` | No commit in 12mo → CAUTION |
| Security | `metrics.security.osv_vulns` | Any unpatched CVE → CAUTION |
| Code Quality | `metrics.code_quality.scorecard_score`, `.code_review` | CI failing or score < 5 → CAUTION |
| Maturity | `metrics.maturity.releases` | Pre-1.0 or < 3 releases → CAUTION |
| Community | `metrics.community.contributors` | ≤1 contributor → NO-GO |
| Vibe-Code | `metrics.vibe_code.history`, `.countries` | Month spike > 3x prior growth, or one country > 70% → CAUTION |

**Note:** `metrics.libraries_io` is soft signal only — never drives a hard verdict.

## Workflow

1. Call `dep_vet` with the dependency spec.
2. Check `not_found` — if true, report "not found — verify name".
3. Check `total_failure` — if true and no `resolved_source`, emit INCONCLUSIVE.
4. For each metric, check `available` flag. If false, mark `unavailable`.
5. Score each available metric against the thresholds above.
6. Combine via decision tree → 4-line verdict.

## Repo Resolution

- Pass a registry spec (`pypi:x`, `npm:x`, `cargo:x`, …) or a repo URL you've **verified**. Never invent an `owner/repo`.
- Don't know the repo URL? Web-search for the official repo first; only pass one that exists.
- If `not_found` or `total_failure` with no `resolved_source`: make **one** web-search attempt to find the canonical repo, then re-vet with the confirmed URL. If still unresolvable, stop — say "could not resolve `<dep>` to a repository" and emit INCONCLUSIVE.

## Decision Tree

- Any NO-GO → NO-GO
- ≥2 CAUTION (incl. `unavailable`) → CAUTION
- 1 CAUTION → GO with caveat (caveat names the metric)
- All 6 evaluated, all clear → GO
- Insufficient data to call → INCONCLUSIVE

## Verdict Template

Output exactly these 4 lines — this is the full output (JSON only if the caller asks):

VERDICT: GO | CAUTION | NO-GO | INCONCLUSIVE
METRICS: (non-passing or unavailable metrics only)
EVIDENCE: (max 2 lines — each claim traces to a JSON field)
CAVEAT: PASS (timestamp) — heuristic snapshot, not a guarantee

## Reliability Rules

- Every EVIDENCE claim must trace to a field in the tool's JSON output. No inference from stars/forks alone.
- `unavailable` is not `pass` — it appears in METRICS and downgrades GO → CAUTION.
- Never fabricate a verdict from missing data; emit INCONCLUSIVE.
- A guessed-but-existing repo returns data for the wrong project — resolve only via registry spec or verified URL (see Repo Resolution).
- GitLab repos: GitHub API and OSS Insight are unavailable; Scorecard works. Partial coverage → CAUTION.
- Maven: repo resolution is unreliable; `resolved_source` may be null → INCONCLUSIVE.

## Non-Repo Dependencies (zip files, arbitrary URLs)

When `resolved_source` is null (no GitHub/GitLab repo found):
1. Emit INCONCLUSIVE — never GO or CAUTION without repo data.
2. Run out-of-band provenance checks (not automated by the tool):
   - Published SHA256 on an independent channel
   - GPG/cosign/minisign signature verification
   - TLS certificate validity
   - Domain age and ownership (whois)
   - How the URL was obtained (trusted source?)
   - Archive content scan (executables, `curl|sh`, symlinks, traversal)
