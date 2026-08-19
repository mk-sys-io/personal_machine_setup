# Pi provisioning — 6 providers (OpenCode Zen, NIM, OpenRouter, Z.ai, NaraRouter, Google AI Studio)

## What

Provision Pi with **6 providers** using a live extension + one `make pi`:

| Provider | Auth method | Model discovery | Free tier |
|---|---|---|---|
| OpenCode Zen | `pi-setup auth` | Static curated list | Unlimited |
| NVIDIA NIM | `pi-setup auth` | Live probe | Credit-based |
| OpenRouter | `pi-setup auth` | Auto-fetched free models | ~20 models, 1-5 req/min |
| Z.ai | `pi-setup auth` | Live probe | 2 free Flash models |
| NaraRouter | `pi-setup auth` | Auto-fetched free models | 10M tokens/day, 10 models |
| Google AI Studio | `pi-setup auth` | Live probe (3-layer filter) | Rate-limited per model |

Default model stays `opencode/big-pickle`. All providers are free-tier only.
One `pi-setup` CLI (batch key entry, model probing, curated dir) plus
`make pi` makes multi-provider login reproducible on a fresh machine.

## Why

Pi's baked catalogs are stale and show paid models by default. The extension
replaces them with a chosen set across 6 providers: Zen with a static free-only
list (no catalog fetch, no hang risk), NIM with a live catalog filtered to
what we chose, OpenRouter with auto-fetched free models, Z.ai with a live
probe, NaraRouter with auto-fetched free models, and Google AI Studio with a
custom catalog fetcher + live probe. `/model` never hangs or leaks unwanted models.

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
make pi                          # deploy extension + settings seed
pi-setup auth                    # prompt + validate all 6 API-key providers (additive);
                                 # then offers to probe NIM + fetch free-model lists
pi-setup probe --write    # re-probe NIM / fetch free-model lists on demand
pi                               # /model → default opencode/big-pickle
```

`make pi` is idempotent. The extension copy excludes `*/curated/*`, then the
**static Zen list** `dev/pi/extensions/live/curated/opencode.json` is copied
into `~/.pi/agent/extensions/live/curated/opencode.json`. All other curated
files (NIM, OpenRouter, Z.ai, NaraRouter) are **generated at runtime** by
`pi-setup probe --write` — no static seeds are shipped in the repo.
`make pi` also copies `settings.seed.json` into
`~/.pi/agent/settings.json` only-if-absent (no merge — `lastChangelogVersion`
is an internal Pi field it re-writes itself). Note: `make pi` covers the
extension + settings; the `pi-setup` tool deploys via
`make dev` (tools loop, `tools/pi-setup.py` → `~/.local/bin/pi-setup`). It
is named `pi-setup` deliberately — `pi` is the Pi agent binary (npm global),
and `~/.local/bin` outranks `/usr/bin` in PATH, so a `pi` binary there would
shadow the agent.

## `/login` vs `pi-setup auth`

- **`/login`** — Pi's first-party interactive login (API keys + OAuth
  subscriptions, token auto-refresh).
- **`pi-setup auth`** — batch path for reproducible setup: prompts only for the
  6 API-key providers (opencode, nim, openrouter, zai, nararouter, gemini),
  validates live, writes `auth.json` per-provider (resume-safe). Additive by
  default; `--force` re-prompts; `--provider opencode nim openrouter` filters.
  After a successful run it offers to probe NIM + fetch free-model lists.
- **`pi-setup auth --reset`** — backs up `auth.json` (`.bak`, 0600), wipes ALL
  registered entries (API keys + OAuth tokens), then re-prompts all providers.
  There is no batch "logout all" CLI, so this is the clean-slate path.
- **`pi-setup auth check`** — shells out to the provider's own `pi auth check`
  against the stored credential and reports which providers pass.
- **`--provider <id>...`** — filters any `pi-setup` subcommand; safe to put
  before or after the subcommand (id list stops at command words like `add`).

## Key resolution

Precedence (Pi): `--api-key` flag > `auth.json` credential > env var >
`models.json` provider apiKey. Stored credential wins.

`auth.json` accepts three key forms: literal string, `$ENV` reference, and
`!command` (executed at read time, cached for the process lifetime) — the
documented key-manager path (e.g. `!op read ...`). `pi-setup` has a reserved
`get_key` seam for a future `--source file|manager` flag.

## Curation workflow

Providers are curated differently, by design:

- **Zen (static)** — `dev/pi/extensions/live/curated/opencode.json` ships in
  the repo as a hand-picked list of exact free-tier ids and is copied into
  place by `make pi`. The extension registers those ids verbatim — no catalog
  fetch,   no probing, no network. New free models are added by editing the repo
  file and re-running `make pi`.
- **NIM (probe-generated)** — `~/.pi/agent/extensions/live/curated/nim.json`
  is written by `pi-setup` probing the live catalog with a real chat request
  per model (15 s timeout, only fast models survive). Each probe writes fresh
  — no merge with previous results.
- **OpenRouter (auto-fetched)** — `pi-setup probe openrouter`
  fetches the public catalog, filters free models (pricing=$0), writes to
  `curated/openrouter-free.json`. No probing needed — free models are reliable.
- **Z.ai (probe-generated)** — `pi-setup probe zai` fetches the
  full catalog (auth needed), pre-filters by `NON_CHAT_KEYWORDS`, then probes
  each model with a real chat request (15 s timeout, tool-calling + streaming).
  Z.ai's API does not expose pricing metadata, and name heuristics are
  unreliable (e.g. "flash" matches both free `glm-4.7-flash` and paid
  `glm-4.7-flashx`). Writes survivors to `curated/zai.json`.
- **NaraRouter (auto-fetched)** — `pi-setup probe nararouter`
  fetches from `/api/plans`, extracts free-tier model list, writes to
  `curated/nararouter-free.json`. The free tier is stable (10 models).
  `index.ts` uses `makeRefreshModels` to filter against curated patterns.
- **Google AI Studio (probe-generated)** — `pi-setup probe gemini`
  fetches the native catalog (with `?key=` auth, not `Authorization: Bearer`),
  filters by `supportedGenerationMethods` containing `generateContent`, excludes
  non-chat models via `NON_CHAT_KEYWORDS`, then probes each model with a real
  chat request (15 s timeout, tool-calling + streaming). Writes to
  `curated/gemini.json`. Re-probe after any Google API changes.

Commands (default provider: `nim`):

- `pi-setup probe [--write]` — preflight connectivity check, then:
  for NIM: probe live catalog with real chat requests (15 s timeout, only fast
  models survive). For gemini/zai: probe catalog with `NON_CHAT_KEYWORDS`
  pre-filter and live chat requests. For openrouter/nararouter: auto-fetch
  free models from catalog. `--write` saves the curated file fresh each time
  (no merge with previous results). `probe opencode` refuses — Zen is
  static.
- `pi-setup dir` — print the curated directory path; the JSON keep-sets
  live there and can be edited by hand.
- Catalog/auth GETs retry up to 3 times (1s/2s backoff) on transient network
  errors and send an explicit User-Agent; HTTP rejections are reported as
  their status code, not as "offline".

### Zen — free-only policy

The static list contains only free-tier ids; paid models are absent by
construction (no probing involved). Escape hatch: edit
`dev/pi/extensions/live/curated/opencode.json` to keep one explicitly, then
`make pi`. Default model stays `opencode/big-pickle`.

### NIM — see-all and pick

NVIDIA has no free/paid split (credit-based free tier). A probe keeps every
working model; nothing is hidden unless you trim the `patterns` array in the
curated file by hand.

## Zen tradeoff — static list instead of probing

Zen's free-tier probing was measured and **does not work reliably**, so we
ship a static list instead of a probe:

- **Cloudflare blocks urllib** — `Python-urllib` user-agents get HTTP 403 on
  Zen's `/chat/completions` (and eventually `/models`); the Node
  `fetch` in the extension hits the same wall. A browser user-agent passes,
  but the `pi-setup` tool is stdlib-only and probing with a browser UA is a
  moving target.
- **Free-tier latency kills the timeout** — streaming first-token times are
  frequently 2–20 s+ (e.g. `deepseek-v4-flash-free` ~20 s, `mimo-v2.5-free`
  ~8 s, `ling-3.0-*-free` transiently 503). NIM's 15 s probe bound would
  silently drop working models; raising it makes setup feel broken.
- **Cheap** — no live probe = no catalog fetch, no per-model chat requests,
  no false negatives, no `/model` hangs from a flaky filter.

**The tradeoff:** the list can drift stale. New Zen free models are not
auto-discovered — add their exact id to
`dev/pi/extensions/live/curated/opencode.json` and `make pi`. NIM's probe path
is unchanged because it works.

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
  `toStaticModel(cfg, id)`. `pi-setup probe` is not involved.
  *Examples: OpenCode Zen (hand-picked, no catalog needed)*

- **Yes, in OpenAI format** (`{data: [{id: ...}]}`) → continue to step 3.
  *Examples: NIM, OpenRouter, Z.ai, NaraRouter*

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
`pi-setup probe` fetches from this endpoint; `index.ts` uses
`makeRefreshModels`.
*Example: NaraRouter (`/api/plans` free-tier list)*

**3b. Pricing metadata in API response** — Each model object includes cost
fields. Filter by price = 0. `pi-setup probe` fetches the full catalog,
filters by pricing; `index.ts` uses `makeRefreshModels`.
*Example: OpenRouter (`pricing.prompt == "0" && pricing.completion == "0"`)*

**3c. No reliable API-level filter** — The catalog doesn't expose pricing
or a free-tier flag. Name-based heuristics are unreliable (e.g. Z.ai
"flash" matches both free `glm-4.7-flash` and paid `glm-4.7-flashx`).
→ **Probe.** Use the general probe path (step 4).
*Example: Z.ai*

### 4. Probe the live catalog

When step 3 can't reliably identify free models, probe each model (after
step 1 filtering) with a live chat request (tool-calling + streaming, 15 s
timeout). Models returning 200 with SSE chunks within the timeout are
working and usable.

Write survivors to the curated file. `index.ts` uses `makeRefreshModels`
(or a custom variant for non-OpenAI formats): fetches the full catalog,
filters against curated patterns (exact surviving model IDs).

Probing catches: paid models (402/403), deprecated models (404), slow
models (timeout), and broken endpoints. Re-run `pi-setup probe --write`
when the provider's catalog changes.
*Examples: NIM (credit-based, no free/paid split), Z.ai (no pricing
metadata), Google AI Studio (custom fetcher + probe)*

### Summary table

| Strategy | `NON_CHAT` pre-filter | `pi-setup probe` | `index.ts` registration | Curated file role |
|---|---|---|---|---|
| Static | N/A (hand-picked) | Not involved | `models: readPatterns(...).map(toStaticModel(cfg, id))` | Literal model IDs |
| Free endpoint | Apply to catalog | Fetches free endpoint, writes IDs | `models: [], refreshModels: makeRefreshModels(...)` | Filter patterns |
| Pricing filter | Apply to catalog | Fetches catalog, filters by price, writes IDs | `models: [], refreshModels: makeRefreshModels(...)` | Filter patterns |
| Probe | Apply to catalog | Probes each model, writes survivors | `models: [], refreshModels: makeRefreshModels(...)` | Exact surviving IDs |
| Custom fetcher + probe | Apply to transformed catalog | Same as probe | `models: [], refreshModels: makeRefreshModels*()` | Exact surviving IDs |

## Extension internals

`extensions/live/index.ts` (TS, loaded via jiti — no build step):

- Registers 6 providers via `pi.registerProvider`: `opencode` (override),
  `nim`, `openrouter`, `zai`, `nararouter`, `gemini`.
- **`opencode` is registered with `models` only** — the exact ids from
  `curated/opencode.json`, built at load time. No `refreshModels`, no catalog
  fetch, no network. (Pi treats `models` as a full replacement for a
  provider's list, so the paid built-ins never appear.)
- **`nim` uses `refreshModels`**: resolves the key (stored credential →
  provider env var), fetches `GET <baseUrl>/models`, maps to Pi model shape,
  filters through `curated/nim.json` patterns (exact id or glob).
- **`openrouter` uses `refreshModels`**: fetches catalog, filters against
  curated patterns (`openrouter-free.json` — free model IDs identified by
  pricing metadata in `pi-setup probe openrouter`). Free models are
  reliable so no probing is needed.
- **`zai` uses `refreshModels`**: fetches catalog, filters against curated
  patterns (probe-generated exact IDs). Z.ai's API does not expose pricing
  metadata, so free models are identified by live probing.
- **`nararouter` uses `refreshModels`**: fetches from `/api/plans`,
  filters against curated patterns (free-tier model IDs). The free tier
  is stable (10 models).
- **`gemini` uses a custom `refreshModels`**: `fetchCatalogGemini()`
  transforms the non-OpenAI response (`{models: [{name: "models/..."}]}`)
  to `RawModel[]`, then filters against curated patterns (probe-generated
  exact IDs). Uses `?key=` auth instead of `Authorization: Bearer`.
- Catalog fetch is bounded by a 15 s `AbortController`; on failure it
  falls back to the persisted catalog for NIM/OpenRouter/Z.ai (via
  `context.publish({ persist })`) or shows nothing for Gemini (`fallbackToStored:
  false`). Missing/empty curated file → strict (nothing shown) + console hint.
- Curated files are re-read on every `refreshModels` (NIM, OpenRouter,
  Z.ai, NaraRouter, Gemini). If a curated file is missing (before first
  probe), the provider shows no models and logs a warning. The Zen static
  list applies on extension reload.

## File inventory

| Path | Role |
|---|---|
| `dev/pi/extensions/live/index.ts` | Live extension (TS; loaded via jiti) |
| `dev/pi/extensions/live/curated/opencode.json` | **Static** Zen free-only list (shipped, copied by `make pi`) |
| `dev/pi/extensions/live/curated/nim.json` | **Probe-generated** NIM models (written by `pi-setup probe`, 15 s timeout) |
| `dev/pi/extensions/live/curated/openrouter-free.json` | **Probe-generated** OpenRouter free models (written by `pi-setup probe`) |
| `dev/pi/extensions/live/curated/zai.json` | **Probe-generated** Z.ai models (written by `pi-setup probe`, 15 s timeout) |
| `dev/pi/extensions/live/curated/nararouter-free.json` | **Probe-generated** NaraRouter free models (written by `pi-setup probe`) |
| `dev/pi/extensions/live/curated/gemini.json` | **Probe-generated** Google AI Studio models (written by `pi-setup probe`) |
| `dev/pi/settings.seed.json` | Settings seed (defaults, telemetry off) |
| `tools/pi-setup.py` | `pi-setup` CLI (auth + model probe + curated dir) → `~/.local/bin/pi-setup` |
| `dev/pi/package.json` + `tsconfig.json` | Dev-only typecheck scaffolding (not deployed) |
| `~/.pi/agent/settings.json` | Seeded by `make pi` (copy only-if-absent) |
| `~/.pi/agent/auth.json` | Wiped + written by `pi-setup auth --reset` |
| `~/.pi/agent/extensions/live/curated/nim.json` | Runtime NIM allowlist (probe-generated via `pi-setup probe --write`) |
| `~/.pi/agent/extensions/live/curated/openrouter-free.json` | Runtime OpenRouter free-model list (probe-generated) |
| `~/.pi/agent/extensions/live/curated/zai.json` | Runtime Z.ai free-model list (probe-generated) |
| `~/.pi/agent/extensions/live/curated/nararouter-free.json` | Runtime NaraRouter free-model list (probe-generated) |
| `~/.pi/agent/extensions/live/curated/gemini.json` | Runtime Google AI Studio model list (probe-generated via `pi-setup probe --write`) |

## Maintenance

- **New Zen free model** → add the exact id to
  `dev/pi/extensions/live/curated/opencode.json`, then `make pi` — see the
  [Zen tradeoff](#zen-tradeoff--static-list-instead-of-probing).
- **NIM model list churn** → `pi-setup probe --write` (re-probes and
  writes fresh; no merge with previous results).
- **OpenRouter free model changes** → `pi-setup probe --write`
  re-fetches free models from the live catalog.
- **Z.ai model changes** → `pi-setup probe zai --write`
  re-probes the live catalog (no pricing metadata available).
- **NaraRouter model changes** → `pi-setup probe --write` re-fetches
  from `/api/plans`.
- **Google AI Studio model changes** → `pi-setup probe gemini --write`
  re-probes the live catalog. Google may shut down models without notice
  (e.g. Gemini 2.0 Flash, June 2026).
- **Provider keys** → `pi-setup auth --force` re-prompts;
  `pi-setup auth --reset` wipes.
- **Extension changes** → edit `index.ts`, `tsc -p dev/pi --noEmit` (needs
  `npm install` in `dev/pi` first on a fresh machine), `make pi`, `/reload`.

## Tips

- Run `pi-setup auth` before `pi` so `/login` isn't needed for API-key
  providers.
- Default model `opencode/big-pickle` is adjustable via `/model`.
- `enableInstallTelemetry: false` is seeded; `enableAnalytics` stays off.
- Free-tier provider suggested priority: OpenCode Zen → NIM → OpenRouter →
  NaraRouter → Z.ai → Google AI Studio.

## Future enhancements

Planned but **not implemented**:

- **Probe operation logging** — record NIM probe runs (per-model status,
  latency, keyword-skips) to a log file so a probe history can be reviewed
  after the fact instead of only a terminal summary.
