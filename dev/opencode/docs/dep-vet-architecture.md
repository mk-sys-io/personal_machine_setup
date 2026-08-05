# Dependency Vetting Architecture

## 1. Architecture Overview

Five-layer data flow:

1. **Trigger** (`~/.config/opencode/AGENTS.md`) — when agent is about to suggest a new third-party dependency, it loads the `dep-vet` skill
2. **Tool** (`~/.config/opencode/tools/dep_vet.ts` + `dep_vet.py`) — deterministic data collection via REST APIs; all edge-case handling lives here
3. **Skill** (`~/.config/opencode/skills/dep-vet/SKILL.md`) — judgment layer: metric thresholds, decision tree, verdict template
4. **Install** (`packages/go_installs.txt`, `packages/apt.txt`, `lib/20-packages.sh`) — pre-installs osv-scanner (optional post-install scan)
5. **Documentation** (this file + `dev/opencode/README.md`) — reference for humans modifying the system

## 2. Trigger Scope

Dependencies with a public **GitHub or GitLab** repository. This covers npm, PyPI, crates.io, Go modules, and CLI tools. The tool resolves repos from registry metadata when the agent doesn't know the URL.

## 3. File Inventory

### In `~/.config/opencode/` (deploy-time, outside repo)

| File | Role |
|---|---|
| `AGENTS.md` | Trigger rule — loads `dep-vet` skill when suggesting deps |
| `tools/dep_vet.ts` | Custom tool definition (TS wrapper, Zod-validated args) |
| `tools/dep_vet.py` | Data collection script (python3 stdlib, urllib+json) |
| `skills/dep-vet/SKILL.md` | Judgment layer — thresholds, decision tree, verdict template |

### In `linux_setup/`

| File | Role |
|---|---|
| `dev/opencode/tools/dep_vet.py` | Source of truth for data collection script |
| `dev/opencode/tools/dep_vet.ts` | Source of truth for tool wrapper |
| `dev/opencode/skills/dep-vet/SKILL.md` | Source of truth for skill |
| `dev/opencode/AGENTS.md` | Source of truth for trigger |
| `dev/opencode/commands/vet.md` | `/vet` command definition |
| `dev/opencode/tsconfig.json` | Typecheck config for `dep_vet.ts` (not deployed) |
| `dev/opencode/types/opencode-env.d.ts` | Ambient decls for `@opencode-ai/plugin`/`path`/`Bun`/`import.meta.dir` (not deployed) |
| `packages/apt.txt` | Adds `jq`; moves `nodejs`+`npm` to prerequisites |
| `packages/go_installs.txt` | Adds `osv-scanner` |
| `packages/npm_packages.txt` | npm globals (`typescript@5.9.3`, `typescript-language-server`, `prettier`) via `install_npm_packages()` |
| `lib/20-packages.sh` | Install functions (go_installs, apt, github, etc.) |
| `dev/opencode/docs/dep-vet-architecture.md` | This file |

### Deploy

`make dev` copies everything in `dev/opencode/` (except `docs/`, `README.md`, and the typecheck-only `tsconfig.json` + `types/`) into `~/.config/opencode/`. Typecheck via `tsc -p dev/opencode`. No additional sync mechanism needed.

## 4. Implicit Assumptions

- **python3 on PATH** — `dep_vet.py` uses only stdlib (urllib, json, re, datetime)
- **`@opencode-ai/plugin` v1.16.2 installed** — already in `~/.config/opencode/package.json`
- **Scorecard REST API free, no auth** — `GET /projects/github.com/{owner}/{repo}` via `api.scorecard.dev`, CDN-cached via Fastly
- **GitHub API free, token-optional** — 60/hr unauthenticated, 5000/hr with `GITHUB_TOKEN`
- **OSS Insight free, no auth** — 600/hr; only `/stargazers/history` and `/stargazers/countries` verified
- **Libraries.io free, no auth** — `dependents_count` as soft signal only; free tier 429s after ~2 requests
- **OSV API free, no auth** — `POST /v1/query`; ecosystem must use exact casing (PyPI, npm, Go, crates.io, RubyGems, Maven)
- **No SLA on any API** — all are best-effort public endpoints
- **No private registries** — all deps from public registries (npm, PyPI, crates.io, proxy.golang.org)

## 5. Key Design Decisions

- **Custom tool for data collection** — deterministic, testable, type-hinted; eliminates LLM-interpreted curl/jq fragility
- **Skill for judgment** — thresholds and decision tree stay in SKILL.md where the LLM can reason about them
- **All backends REST** — zero CLI deps; Scorecard + GitHub API + OSS Insight + OSV + Libraries.io (soft)
- **OSV sole hard gate for Security** — unpatched CVEs block
- **Libraries.io never drives hard verdicts** — data unvalidated, soft signal only
- **Registry resolution in tool** — PyPI `project_urls`, npm `repository`, crates.io `repository`, rubygems `source_code_uri`

## 6. Backend Topology

```
Scorecard   → api.scorecard.dev   — no auth — CDN-cached
GitHub API  → api.github.com      — optional GITHUB_TOKEN — 60/hr unauth, 5000/hr auth
OSS Insight → api.ossinsight.io   — no auth — 600/hr
OSV         → api.osv.dev         — no auth — best-effort
Libraries.io→ libraries.io/api    — no auth — free tier (unvalidated, 429s quickly)
```

## 7. Metric Thresholds & Verdict Logic

| Category | Source (in JSON) | Threshold |
|---|---|---|
| Activity | `metrics.activity.days_since_push`; `metrics.code_quality.maintained` | No commit in 12mo → CAUTION |
| Security | `metrics.security.osv_vulns` | Any unpatched CVE → CAUTION |
| Code Quality | `metrics.code_quality.scorecard_score`, `.code_review` | CI failing or score < 5 → CAUTION |
| Maturity | `metrics.maturity.releases` | Pre-1.0 or < 3 releases → CAUTION |
| Community | `metrics.community.contributors` | ≤1 contributor → NO-GO |
| Vibe-Code | `metrics.vibe_code.history`, `.countries` | Month spike > 3x prior, or one country > 70% → CAUTION |

**Verdict logic**:
- Any **NO-GO** → overall NO-GO
- Two or more **CAUTION** (incl. `unavailable`) → overall CAUTION
- One **CAUTION** → GO with caveat
- All clear, all 6 evaluated → GO
- Insufficient data → INCONCLUSIVE

## 8. Verdict Output Format

```
VERDICT: GO | CAUTION | NO-GO | INCONCLUSIVE
METRICS: (non-passing or unavailable metrics only)
EVIDENCE: (max 2 lines — each claim traces to a JSON field)
CAVEAT: PASS (timestamp) — heuristic snapshot, not a guarantee
```

## 9. Reliability Rules

- Every EVIDENCE claim must trace to a field in the tool's JSON output. No inference from stars/forks alone.
- `unavailable` is not `pass` — it appears in METRICS and downgrades GO → CAUTION.
- Never fabricate a verdict from missing data; emit INCONCLUSIVE.
- **Repo resolution rules live in SKILL.md (`## Repo Resolution`) — single source of truth.** AGENTS.md carries only a one-line guardrail; `vet.md` references the skill workflow. Do not restate the rules elsewhere.
- GitLab repos: GitHub API and OSS Insight are unavailable; Scorecard works. Partial coverage → CAUTION.
- Maven: repo resolution is unreliable; `resolved_source` may be null → INCONCLUSIVE.

## 10. Edge Cases Handled in `dep_vet.py`

| Case | Behavior |
|---|---|
| Release-asset URL (zip/tar.gz) | Extracts `o/r` from URL path, vets the source repo |
| Registry spec (`pypi:`, `npm:`, etc.) | Resolves repo from registry metadata, then vets |
| Bare name (no platform prefix) | Tries pypi then npm resolution order |
| Arbitrary non-repo URL | `resolved_source: null` → skill emits INCONCLUSIVE + provenance checklist |
| Trailing `.git` in repo name | Stripped via regex (`\.git$`), not char-set rstrip (prevents e.g. `TypeScrip`) |
| GitHub 404 | `not_found: true` **and** `total_failure: true` (strict semantics) |
| GitHub 403/429 | `unavailable` + "set GITHUB_TOKEN" hint |
| Scorecard 404 on existing repo | `unavailable` (not indexed) — NOT not_found |
| Scorecard missing check | Per-field null (e.g. CI-Tests absent on some repos) |
| OSV ecosystem casing | Uses exact OSV strings (PyPI, not pypi) |
| Libraries.io 429/404 | `unavailable` (soft signal) |
| Per-call timeout | 10s; `total_failure` = no repo resolved, or resolved repo 404s |

## 11. Related Files

- `~/.config/opencode/AGENTS.md` — trigger rule
- `~/.config/opencode/tools/dep_vet.ts` — tool definition
- `~/.config/opencode/tools/dep_vet.py` — data collection script
- `~/.config/opencode/skills/dep-vet/SKILL.md` — judgment layer
- `~/.config/opencode/commands/vet.md` — `/vet` command

## 12. Standalone Tooling — Change List (2026-08-05)

Future enhancements to ship dep-vet as a standalone OSS skill/tool (not a product). Scoped: logging, multi-repo validation, tests, CI/CD, docs, extraction.

**1. Logging** (at tool-call time + internal trace)

- [ ] **Tool-call record** — every invocation appends one JSONL line: `{ts, input, version, resolved_source, platform, ecosystem, not_found, total_failure, errors, key metrics}`. CLI flag `--log FILE`, agent-agnostic (plain CLI, skill, or opencode tool). OpenCode TS wrapper passes a stable path (e.g. `~/.config/opencode/logs/dep-vet.jsonl`).
- [ ] **Internal trace** — `--trace` (verbose): logs resolution path, each API call (endpoint, status, latency, rate-limit headers), and failure reasons — for debugging unresolved deps, 429s, and API drift. Separate stream/file; never mixed into the JSONL call ledger.
- [ ] Future (not v1): ledger feeds `--revet` for post-adoption CVE re-checks.

**2. Multi-repo validation corpus**

- [ ] `tests/corpus/` — golden set (mocked): GO = `google/osv-scanner` (releases ≥3), `psf/requests`, `BurntSushi/ripgrep`; CAUTION = archived repo + repo with CVEs; NO-GO = `trustoss-cli` (1 contributor), 0-release repo; not_found/total_failure = `socketdev/socket` (404); INCONCLUSIVE = maven artifact + arbitrary non-repo URL; edge matrix = release-asset URL, all 6 registry specs, bare name, `.git` suffix, GitLab.
- [ ] `scripts/validate_corpus.py` — live run over N real repos, pass/fail report (used by CI weekly).

**3. Tests (pytest)**

- [ ] Unit tests, mocked `http_get` (no network): parsing + registry-resolution matrix, flag semantics (404 → not_found+total_failure, 403, scorecard-not-indexed), metric mapping.
- [ ] Regressions for 2026-08-05 fixes: release count ≥3 (`per_page=100`); 404 → `"not found"` error label.

**4. CI/CD**

- [ ] `.github/workflows/ci.yml` — ruff + basedpyright + pytest (mocked).
- [ ] Weekly scheduled job: live validation corpus → drift detection (Scorecard / OSS Insight / Libraries.io).
- [ ] Later: release workflow on version tags.

**5. Docs**

- [ ] `README.md` — pitch, per-agent quickstart (CLI / opencode tool / claude-code skill), input+output formats, data sources + rate limits, "What it does NOT do", maintenance stance.
- [ ] `LICENSE` (MIT) + SPDX headers; `CHANGELOG.md`; adapt this architecture doc.

**6. Extraction**

- [ ] Standalone repo = new source of truth. Layout: `src/dep_vet.py`, `skills/dep-vet/SKILL.md`, `tests/`, `scripts/`, `docs/`, `README.md`, `LICENSE`, `pyproject.toml` (dev-only deps; runtime stdlib-only), `.github/`.
- [ ] `SKILL.md` "How to Run" → agent-agnostic bash (`python3 dep_vet.py <spec> [--log …]`); opencode tool becomes optional integration.
- [ ] linux_setup keeps only opencode integration (`dep_vet.ts`, AGENTS.md, `/vet`, tsconfig) and references the standalone repo; wrapper logs via `--log`.

**Sequencing:** 1 → 2+3 → 4 → 5 → 6 (extract).
