---
name: dep-vet
description: Vets third-party OSS dependencies before use. Checks activity, security, quality, maturity, community, and vibe-code signals. Produces a structured verdict.
license: MIT
compatibility: opencode, claude-code
allowed-tools: Read, Grep, Glob, Bash
metadata:
  version: "1.0.0"
  domain: security
  triggers: dependency, vet, github repo
  role: specialist
  scope: supply-chain
  output-format: structured-verdict
---

# Dependency Vetting

Vets third-party OSS dependencies across 6 metric categories before the agent writes dependent code.

## Metric Definitions

| Category | Trigger |
|---|---|
| Activity | No commit in 12mo → CAUTION |
| Security | Any unpatched CVE → CAUTION |
| Code Quality | CI failing or <30% coverage → CAUTION |
| Maturity | Pre-1.0 or <3 releases → flag |
| Community | ≤1 contributor → NO-GO |
| Vibe-Code | Anomalous growth → CAUTION (CLI only) |

## Workflow

1. Resolve repo URL (known value, registry API, or /vet input)
2. Check `which trustoss` — CLI if present, REST if not
3. TrustOSS: CLI → `trustoss analyze <owner>/<repo>` or REST → curl
4. Scorecard: always REST → `GET https://api.scorecard.dev/projects/github.com/{owner}/{repo}`
5. OSV: always REST → `POST https://api.osv.dev/v1/query` with ecosystem
6. Combine into verdict using decision tree

## CLI Path

```bash
trustoss analyze <owner>/<repo>        # core scan
# trustoss deep-scan, trustoss star-audit also available for bus factor / fake-stars
osv-scanner scan -r .                  # optional transitive scan after install
```

## REST Fallback

If `trustoss` is not on PATH:

```bash
TrustOSS: curl -s "https://trustoss.org/api/analyze?repo=https://github.com/<o>/<r>" | jq .
Scorecard: curl -s "https://api.scorecard.dev/projects/github.com/<o>/<r>" | jq .score
OSV: curl -s -X POST -d '{"version":"latest","package":{"name":"<pkg>","ecosystem":"<eco>"}}' "https://api.osv.dev/v1/query" | jq '.vulns[]?.id'
```

**Ecosystem mapping:** npm→npm, Python→pip, Go→Go, Rust→cargo, Ruby→gem.
If the dep's ecosystem isn't listed, try omitting the `ecosystem` field, or skip OSV.

## Decision Tree

Combine metric results into an overall verdict:

- Any **NO-GO** → overall NO-GO
- Two or more **CAUTION** → overall CAUTION
- One **CAUTION** → GO with caveat
- All clear → GO

## Verdict Template

Output exactly 4 lines:

VERDICT: GO | CAUTION | NO-GO
METRICS: (summary of which categories hit what)
EVIDENCE: (max 2 lines — what specifically triggered the verdict)
CAVEAT: PASS (timestamp) — heuristic snapshot, not a guarantee

Use JSON for detail; surface only failures and warnings.

## Error Handling

- **Partial failure** (one API down): skip that metric, note "unavailable", continue
- **Total failure** (all APIs down): report "APIs unreachable — cannot vet"
- **Dep not found** (registry returns empty): report "not found — verify name"
