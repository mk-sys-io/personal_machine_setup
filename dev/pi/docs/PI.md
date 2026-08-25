# Pi provisioning — 5 providers (OpenRouter, NaraRouter, OpenCode Zen, NIM, Google AI Studio)

## What

Provision Pi with **5 providers** using a live extension + one `make dev`:

| Provider | Auth method | Model discovery |
|---|---|---|
| OpenRouter | `pi-setup auth` | `fetch` — free-model list download |
| NaraRouter | `pi-setup auth` | `fetch` — free-tier list download |
| OpenCode Zen | `pi-setup auth` | `probe` — live chat requests (free-tier classification) |
| NVIDIA NIM | `pi-setup auth` | `probe` — live chat requests |
| Google AI Studio | `pi-setup auth` | `probe` — live chat requests (3-layer filter) |



Default model stays `opencode/big-pickle`. All providers are free-tier only.
One `pi-setup` CLI (batch key entry, model discovery, curated dir) plus
`make dev` makes multi-provider login reproducible on a fresh machine.

## Why

Pi's baked catalogs are stale and show paid models by default. The extension
replaces them with a chosen set across 5 providers: Zen with a live-probed
free-only list, NIM with a live catalog filtered to what we chose, OpenRouter
with fetched free-model lists (`fetch`), NaraRouter likewise, and
Google AI Studio with a custom catalog fetcher + live probe. `/model` never
hangs or leaks unwanted models.

### Why not Cloudflare Workers AI?

Cloudflare Workers AI offers 84 models with a 10K neuron/day free tier, but is
excluded for two reasons:

1. **Pi integration bug**: After successful authentication, actual model calls
   return 401 despite correct credentials. This suggests Pi's Cloudflare provider
   doesn't correctly pass the `account_id` in the API URL.
2. **Platform-level tool calling bug** ([developer-platform#54](https://github.com/cloudflare/developer-platform/issues/54)):
   Tool call arguments containing UUIDs produce broken control tokens (`<|"|`)
   instead of the actual value. This affects the inference layer (not the models)
   and remains open since April 2026.

## How

```bash
make all                          # dotfiles + dev (Pi extension deploys here)
make dev                          # dev configs + Pi extension + settings seed
pi-setup auth                     # keys for all 5 (additive); then offers
                                  # per-provider discovery (fetch-first order)
pi-setup fetch --all --write      # re-fetch both free lists (fast)
pi-setup probe --all --write      # full live re-probe (30+ min)
                                  # single provider: probe|fetch <id> --write
pi                                # /model → default opencode/big-pickle
```

Full command reference: `pi-setup --help`.

`make dev` is idempotent. **All** curated files are **generated at runtime**
by `pi-setup probe`/`fetch --write` into
`~/.pi/agent/extensions/live/curated/`; no curated seeds ship in the repo
(before the first run a provider shows no models + a console hint).
`make dev` also copies `settings.seed.json` into `~/.pi/agent/settings.json`
only-if-absent (no merge — `lastChangelogVersion` is an internal Pi field it
re-writes itself). The extension + settings and the `pi-setup` tool all
deploy via `make dev` (`tools/pi_setup/` → zipapp → `~/.local/bin/pi-setup`). It is
named `pi-setup` deliberately — `pi` is the Pi agent binary (npm global),
and `~/.local/bin` outranks `/usr/bin` in PATH, so a `pi` binary there would
shadow the agent.

## Credentials

Use `/login` for OAuth/subscription logins (token auto-refresh); use
`pi-setup auth` for the five API keys (batch, live-validated, resume-safe).
Pi has no batch logout-all: `auth --reset [--provider X...]` wipes
credentials and re-prompts; `clean [--yes]` additionally deletes
`models-store.json` — the cached catalog fallback whose stale entries
resurrect unfiltered catalogs after a failed refresh. Everything else:
`pi-setup --help`.


## Key resolution

Precedence (Pi): `--api-key` flag > `auth.json` credential > env var >
`models.json` provider apiKey. Stored credential wins.

`auth.json` accepts three key forms: literal string, `$ENV` reference, and
`!command` (executed at read time, cached for the process lifetime) — the
documented key-manager path (e.g. `!op read ...`).

## Curation workflow

One verb per provider, by design (`--write` always writes fresh — no merge
with previous results):

| Provider | Verb | Curated file | Filter chain |
|---|---|---|---|
| openrouter | `fetch` | `openrouter-free.json` | public catalog → `NON_CHAT_KEYWORDS` → pricing=$0 |
| nararouter | `fetch` | `nararouter-free.json` | `/api/plans` free-tier list |
| opencode | `probe` | `opencode.json` | catalog → keywords → live chat @45 s (`classify_zen`: keep 200/429) |
| nim | `probe` | `nim.json` | catalog → keywords → live chat @15 s |
| gemini | `probe` | `gemini.json` | native catalog → `generateContent` → keywords → live chat @35 s (+1 retry on timeout) |

Mechanics — trust levels, timeouts, retries, classification — are documented
where they live: `pi-setup --help` and the module/function docstrings in
`tools/pi_setup/`. Curated files are consumed by the extension's
`makeRefreshModels` (below); `pi-setup dir` prints their location.


## Adding a provider

When adding a new provider to `pi-setup` + `index.ts`, follow this
checklist to choose the correct curation strategy:

### 1. Pre-filter non-chat models (always first)

Apply `NON_CHAT_KEYWORDS` (embedding, tts, image, video, audio, diffusion,
safety, etc.) to the full catalog. Remove models Pi can never use for agentic
chat. This is the first pass for every provider that has a catalog — static,
probed, or filtered.

### 2. Does the provider expose a model list API?

- **No** (no `/models` endpoint at all) →
  **Static.** Ship a curated file in `dev/pi/extensions/live/curated/`
  with exact model IDs. `index.ts` reads it once at load via
  `toStaticModel(cfg, id)`. `pi-setup` probe/fetch are not involved.
  *Examples: none currently (OpenCode Zen used to be static; it moved to the
  probe path — see `classify_zen` in `tools/pi_setup/probe.py` (HTTP-status free-tier classification))*

- **Yes, in OpenAI format** (`{data: [{id: ...}]}`) → continue to step 3.
  *Examples: NIM, OpenRouter, NaraRouter*

- **Yes, in non-OpenAI format** → **Custom fetcher.** Add a
  provider-specific catalog function that transforms the response to
  `RawModel[]`, then use a provider-specific `makeRefreshModels*` factory.
  Still continue to step 3 for free-model identification.
  *Example: Gemini (`{models: [{name: "models/..."}]}` →
  `fetchCatalogGemini()`)*

### 3. Identify free models

Three sub-paths, checked in order. Use the first that applies:

**3a. Dedicated free endpoint** — Provider exposes an API that returns only
free models. Most reliable — no paid models to accidentally include.
`pi-setup fetch` reads this endpoint; `index.ts` uses
`makeRefreshModels`.
*Example: NaraRouter (`/api/plans` free-tier list)*

**3b. Pricing metadata in API response** — Each model object includes cost
fields. Filter by price = 0. `pi-setup fetch` downloads the full catalog,
filters by pricing; `index.ts` uses `makeRefreshModels`.
*Example: OpenRouter (`pricing.prompt == "0" && pricing.completion == "0"`)*

**3c. No reliable API-level filter** — The catalog doesn't expose pricing
or a free-tier flag. Name-based heuristics are unreliable (e.g. "flash"
can match both free and paid variants of the same family).
→ **Probe.** Use the general probe path (step 4).

### 4. Probe the live catalog

When step 3 can't reliably identify free models, probe each model (after
step 1 filtering) with a live chat request (tool-calling + streaming; per-provider timeout,
15–45 s). Models returning 200 with SSE chunks within the timeout are
working and usable.

Write survivors to the curated file. `index.ts` uses `makeRefreshModels`
(or a custom variant for non-OpenAI formats): fetches the full catalog,
filters against curated patterns (exact surviving model IDs).

Probing catches: paid models (402/403), deprecated models (404), slow
models (timeout), and broken endpoints. Re-run `pi-setup probe --write`
when the provider's catalog changes.
*Examples: NIM (credit-based, no free/paid split), Google AI Studio
(custom fetcher + probe)*

### Summary table

| Strategy | `NON_CHAT` pre-filter | `pi-setup` command | `index.ts` registration | Curated file role |
|---|---|---|---|---|
| Static | N/A (hand-picked) | Not involved | `models: readPatterns(...).map(toStaticModel(cfg, id))` | Literal model IDs |
| Free endpoint | Apply to catalog | `fetch`: reads free endpoint, writes IDs | `models: [], refreshModels: makeRefreshModels(...)` | Filter patterns |
| Pricing filter | Apply to catalog | `fetch`: downloads catalog, filters by price, writes IDs | `models: [], refreshModels: makeRefreshModels(...)` | Filter patterns |
| Probe | Apply to catalog | `probe`: probes each model, writes survivors | `models: [], refreshModels: makeRefreshModels(...)` | Exact surviving IDs |
| Custom fetcher + probe | Apply to transformed catalog | `probe` (same) | `models: [], refreshModels: makeRefreshModels*()` | Exact surviving IDs |

## Extension internals

`extensions/live/index.ts` (TS, loaded via jiti — no build step) registers
all 5 providers via `pi.registerProvider`. Every provider uses
`makeRefreshModels`: fetch the catalog (15 s `AbortController`), map to Pi's
model shape, filter against the curated patterns (re-read on every refresh),
then publish + persist. On fetch failure it falls back to the persisted
catalog — except gemini (`fallbackToStored: false`, nothing shown). A
missing/empty curated file is strict: no models + a console hint.

Gemini deliberately uses the native `google-generative-ai` protocol instead
of the OpenAI-compat shim: Google's shim strictly rejects OpenAI-only fields
(e.g. `store`) with an opaque gzip-masked `400 status code (no body)`; the
native protocol eliminates that bug class and returns readable errors.
`inputTokenLimit`/`outputTokenLimit` are passed through as
`contextWindow`/`maxTokens` (without this, every Gemini model falls back to
pi's 128k default and misreports its window).
pi-setup's probe matches this: gemini models are probed through the same
native `:generateContent` protocol rather than the OpenAI-compat shim.


## File inventory

Repo (`dev/pi/`, deployed by `make dev`):

| Path | Role |
|---|---|
| `extensions/live/index.ts` | Live extension (TS; loaded via jiti) |
| `settings.seed.json` | Settings seed (defaults, telemetry off) |
| `package.json` + `tsconfig.json` | Dev-only typecheck scaffolding (not deployed) |

Tooling:

| Path | Role |
|---|---|
| `tools/pi_setup/` | `pi-setup` CLI (auth + probe/fetch discovery + curated dir), deployed as a zipapp → `~/.local/bin/pi-setup` |

Runtime (`~/.pi/agent/`, not in repo):

| Path | Role |
|---|---|
| `extensions/live/curated/*.json` | All five curated allowlists (`opencode.json`, `nim.json`, `openrouter-free.json`, `nararouter-free.json`, `gemini.json`) — runtime-generated by `pi-setup probe`/`fetch --write`; none ship in the repo |
| `settings.json` | Seeded by `make dev` (copy only-if-absent) |
| `auth.json` | Written by `auth`; wiped by `--reset` / `clean` |
| `models-store.json` | Pi's cached catalog fallback; deleted by `clean` |

## Maintenance

- Extension changes: edit `index.ts`, then `tsc -p dev/pi --noEmit` (needs
  `npm install` in `dev/pi` first on a fresh machine), `make dev`, `/reload`.
- Keys + catalogs: covered by `pi-setup --help` (`auth --force`,
  `auth --reset [--provider X...]`, `clean`,
  `probe|fetch <provider>...|--all --write`).


## Tips

- Run `pi-setup auth` before `pi` so `/login` isn't needed for API-key
  providers.
- Default model `opencode/big-pickle` is adjustable via `/model`.
- `enableInstallTelemetry: false` is seeded; `enableAnalytics` stays off.
- Free-tier provider suggested priority: OpenCode Zen → NIM → OpenRouter →
  NaraRouter → Google AI Studio.

## Future enhancements

Planned but **not implemented**:

- **Probe operation logging** — record probe runs (per-model status,
  latency, keyword-skips) to a log file so a probe history can be reviewed
  after the fact instead of only a terminal summary.
