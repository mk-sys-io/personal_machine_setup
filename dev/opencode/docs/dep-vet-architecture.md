# Dependency Vetting Architecture

## 1. Architecture Overview

Four-layer data flow:

1. **Trigger** (`~/.config/opencode/AGENTS.md`) — when agent is about to suggest a new third-party dependency with a public GitHub/GitLab repo, it loads the `dep-vet` skill
2. **Workflow** (`~/.config/opencode/skills/dep-vet/SKILL.md`) — defines metric categories, decision tree, curl REST commands, verdict templates
3. **Install** (`packages/npm_packages.txt`, `packages/go_installs.txt`, `packages/apt.txt`, `lib/20-packages.sh`) — pre-installs CLI tools for optional fast path
4. **Documentation** (this file + `dev/opencode/README.md`) — reference for humans modifying the system

## 2. Trigger Scope

Dependencies with a public **GitHub or GitLab** repository. This covers npm, PyPI, crates.io, Go modules, and CLI tools. If the agent does not know the repo URL, it fetches it from the registry API before proceeding.

## 3. File Inventory

### In `~/.config/opencode/` (deploy-time, outside repo)

| File | Role |
|---|---|
| `AGENTS.md` | Trigger rule (+5 lines) — loads `dep-vet` skill when suggesting deps |
| `skills/dep-vet/SKILL.md` | Workflow — metric definitions, REST/CLI commands, verdict templates |

### In `linux_setup/`

| File | Role |
|---|---|
| `packages/apt.txt` | Adds `jq`; moves `nodejs`+`npm` to prerequisites |
| `packages/go_installs.txt` | Adds `osv-scanner` |
| `packages/npm_packages.txt` | Adds `trustoss-cli` |
| `lib/20-packages.sh` | Adds `install_npm_packages()` function |
| `dev/opencode/docs/dep-vet-architecture.md` | This file |
| `dev/opencode/README.md` | Entry linking to this file |
| `plans/dep-vet-plan.md` | Implementation plan (deleted after impl) |

## 4. Implicit Assumptions

- **curl + jq on PATH** — both used in every REST API call
- **TrustOSS REST API free, no auth** — `GET /api/analyze?repo=<url>` confirmed working
- **Scorecard REST API free, no auth** — `GET /projects/github.com/{owner}/{repo}` via `api.scorecard.dev`, CDN-cached via Fastly
- **Scorecard REST omits 3 checks at scale** — CI-Tests, Contributors, Dependency-Update-Tool excluded (TrustOSS CLI covers these)
- **OSV API free, no auth** — `POST /v1/query` confirmed working
- **No SLA on any API** — all three are best-effort public endpoints
- **No rate-limit headers exposed** — none of the three return rate-limit headers
- **GITHUB_TOKEN optional** — only the CLI path needs it; REST APIs work without auth
- **Network connectivity** — `curl` can reach all three endpoints from the dev environment
- **No private registries** — all deps from public registries (npm, PyPI, crates.io, proxy.golang.org)

## 5. Key Design Decisions

- **curl + jq REST as default** — zero-install path works immediately; CLI tools are an optional optimization
- **TrustOSS CLI-only features** — `deep-scan` and `star-audit` subcommands have no REST endpoint; the REST combination (TrustOSS analyze + Scorecard + OSV) covers the same signals
- **No MCP server / Custom Tool** — the skill file handles judgment-based workflows better than deterministic code

## 6. CLI vs REST Decision Tree

```
trustoss on PATH?
├─ YES → Use CLI path: trustoss analyze, osv-scanner
└─ NO  → Use REST path: curl trustoss.org/api/analyze, api.scorecard.dev, api.osv.dev/v1/query

Scorecard → always REST (CLI requires GITHUB_TOKEN)
OSV       → always REST (no CLI binary)
```

## 7. Metric Thresholds & Verdict Logic

| Category | Metric | Threshold |
|---|---|---|
| Activity | Commit recency, release cadence | No commit in 12mo → CAUTION |
| Security | Open CVEs (direct) | Any unpatched CVE → CAUTION |
| Code Quality | CI passing, linting, coverage | CI failing or <30% coverage → CAUTION |
| Maturity | Release count, version age, semver | Pre-1.0 or <3 releases → flag |
| Community | Bus factor, contributor count, stars | ≤1 contributor → NO-GO |
| Vibe-Code | Fake-star detection, growth curve | Anomalous growth → CAUTION |

**Verdict logic**:
- Any **NO-GO** → overall NO-GO
- Two or more **CAUTION** → overall CAUTION
- One **CAUTION** → GO with caveat
- All clear → GO

## 8. Verdict Output Format

```
VERDICT: GO | CAUTION | NO-GO
METRICS: (summary of which categories hit what)
EVIDENCE: (max 2 lines — what specifically triggered the verdict)
CAVEAT: PASS (timestamp) — heuristic snapshot, not a guarantee
```

## 9. Limitations

| Limitation | Severity | Mitigation |
|---|---|---|
| Point-in-time snapshot | Medium | Timestamp every verdict; re-run on suspicion |
| Transitive deps not checked at suggestion time | Medium | Optional post-install OSV scan in the skill |
| No runtime/behavioral analysis | Medium | Static analysis only — out of scope |
| New packages penalized (no track record) | Low | Skill flags "pre-1.0" separately, doesn't block on newness alone |
| False sense of security | High | Every verdict includes caveat line |
| No enforcement (agent can ignore) | Medium | Instructional only — enforcement requires hooks |
| GitHub API rate limits | Low | Scorecard REST API is CDN-cached; no rate-limit headers exposed |
| Scorecard REST omits 3 checks at scale | Low | CI-Tests, Contributors, DUT not in REST API; TrustOSS CLI covers these signals |
| LLM context cost of verbose output | Low | Verdict template limits to 5 lines; use JSON for detail |

## 10. Related Files

- `plans/dep-vet-plan.md` — implementation plan (deleted after implementation)
- `~/.config/opencode/AGENTS.md` — trigger rule
- `~/.config/opencode/skills/dep-vet/SKILL.md` — workflow definitions
