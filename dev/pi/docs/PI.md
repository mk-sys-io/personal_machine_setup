# Pi provisioning — OpenCode Zen + NVIDIA NIM

## What

Provision Pi with two working providers — **OpenCode Zen** (free-only) and
**NVIDIA NIM** (credit-based) — using a live extension. Zen is a **static
curated list** (exact model ids shipped in the repo, copied by `make pi`);
NIM is **probe-curated** from its live catalog at setup time. A single
`pi-setup` CLI (batch key entry, NIM probing, allowlist editing) plus one
`make pi` makes multi-provider login reproducible on a fresh machine.

## Why

Pi's baked catalogs are stale and show paid models by default. The extension
replaces them with a chosen set: Zen with a static free-only list (no catalog
fetch, no hang risk), NIM with a live catalog filtered to what we chose and
persisted with fallback on failure — so `/model` never hangs or leaks unwanted
models.

## How

```bash
make pi                          # deploy extension + settings seed
pi-setup auth                    # prompt + validate opencode + nim keys (additive);
                                 # then offers to probe NVIDIA NIM → curated file
pi-setup models probe --write    # re-probe NIM's live catalog on demand
pi                               # /model → default opencode/big-pickle
```

`make pi` is idempotent. The extension copy excludes `*/curated/*`, then the
**static Zen list** `dev/pi/extensions/live/curated/opencode.json` is copied
into `~/.pi/agent/extensions/live/curated/opencode.json`. NIM's curated file
(`nim.json`) is **seeded only-if-absent**: the repo's hand-picked keep-set
reaches fresh machines, but a probe-written runtime file is never clobbered
(probes merge with it instead). `make pi` also copies `settings.seed.json` into
`~/.pi/agent/settings.json` only-if-absent (no merge — `lastChangelogVersion`
is an internal Pi field it re-writes itself). Note: `make pi` covers the
extension + settings; the `pi-setup` tool deploys via `make dev` (tools loop,
`tools/pi-setup.py` → `~/.local/bin/pi-setup`). It is named `pi-setup`
deliberately — `pi` is the Pi agent binary (npm global), and `~/.local/bin`
outranks `/usr/bin` in PATH, so a `pi` binary there would shadow the agent.

## `/login` vs `pi-setup auth`

- **`/login`** — Pi's first-party interactive login (API keys + OAuth
  subscriptions, token auto-refresh). Use it for ad-hoc providers.
- **`pi-setup auth`** — batch path for reproducible setup: prompts only for the 2
  target providers, validates live, writes `auth.json` per-provider
  (resume-safe). Additive by default; `--force` re-prompts;
  `--provider opencode nim` filters. After a successful run it offers to
  probe NVIDIA NIM and generate its curated allowlist.
- **`pi-setup auth --reset`** — backs up `auth.json` (`.bak`, 0600), wipes ALL
  registered entries (API keys + OAuth tokens), then re-prompts opencode +
  nim. There is no batch "logout all" CLI, so this is the clean-slate path.
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

The two providers are curated differently, by design:

- **Zen (static)** — `dev/pi/extensions/live/curated/opencode.json` ships in
  the repo as a hand-picked list of exact free-tier ids and is copied into
  place by `make pi`. The extension registers those ids verbatim — no catalog
  fetch, no probing, no network. New free models are added by editing the repo
  file and re-running `make pi`.
- **NIM (probe-generated)** — `~/.pi/agent/extensions/live/curated/nim.json`
  is seeded by `make pi` (only-if-absent) and written by `pi-setup` probing
  the live catalog with a real, cheap chat request per model, keeping only ids
  that actually respond.

Commands (default `--provider nim`):

- `pi-setup models probe [--write]` — fetch the live NIM catalog, probe each
  chat-eligible model (45 s total-time bound each, 1.5 s pacing between probes
  to avoid NIM worker saturation, non-chat ids pre-filtered by keyword), print
  a working breakdown with the fastest five. `--write` saves the curated file,
  **merging** with any existing patterns so hand-kept models survive re-probes.
  Probing needs a configured key (it sends a real chat request); without one
  it fails fast with a hint to run `pi-setup auth`. `models probe --provider
  opencode` refuses — Zen is static, not probed.
- `pi-setup models dir` — print the curated directory path; the JSON keep-sets
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
  ~8 s, `ling-3.0-*-free` transiently 503). NIM's 45 s probe bound would
  silently drop working models; raising it makes setup feel broken.
- **Cheap** — no live probe = no catalog fetch, no per-model chat requests,
  no false negatives, no `/model` hangs from a flaky filter.

**The tradeoff:** the list can drift stale. New Zen free models are not
auto-discovered — add their exact id to
`dev/pi/extensions/live/curated/opencode.json` and `make pi`. NIM's probe path
is unchanged because it works.

## Extension internals

`extensions/live/index.ts` (TS, loaded via jiti — no build step):

- Registers `nim` and overrides the built-in `opencode` via
  `pi.registerProvider` (docs "Override Existing Provider") so the chosen
  models replace the baked ones.
- **`opencode` is registered with `models` only** — the exact ids from
  `curated/opencode.json`, built at load time. No `refreshModels`, no catalog
  fetch, no network. (Pi treats `models` as a full replacement for a
  provider's list, so the paid built-ins never appear.)
- **`nim` uses `refreshModels`**: resolves the key (stored credential →
  provider env var), fetches `GET <baseUrl>/models`, maps to Pi model shape,
  filters through `curated/nim.json` patterns (exact id or glob). The file is
  seeded by `make pi` (only-if-absent) so a fresh machine shows a working list
  even before the first probe.
- Catalog fetch is bounded by a 15 s `AbortController`; on failure it falls
  back to the persisted catalog (via `context.publish({ persist })`) so the
  selector can't hang (issue #7545). Missing/empty curated file → strict
  (nothing shown) + console hint.
- Curated files are re-read on every `refreshModels` (NIM); the Zen static
  list applies on extension reload.

## File inventory

| Path | Role |
|---|---|
| `dev/pi/extensions/live/index.ts` | Live extension (TS; loaded via jiti) |
| `dev/pi/extensions/live/curated/opencode.json` | **Static** Zen free-only list (shipped, copied by `make pi`) |
| `dev/pi/extensions/live/curated/nim.json` | **Seed** NIM keep-set (hand-picked ids incl. probe-too-slow models; copied only-if-absent by `make pi`) |
| `dev/pi/settings.seed.json` | Settings seed (defaults, telemetry off) |
| `tools/pi-setup.py` | `pi-setup` CLI (auth + NIM probe + curated dir) → `~/.local/bin/pi-setup` |
| `dev/pi/package.json` + `tsconfig.json` | Dev-only typecheck scaffolding (not deployed) |
| `~/.pi/agent/settings.json` | Seeded by `make pi` (copy only-if-absent) |
| `~/.pi/agent/auth.json` | Wiped + written by `pi-setup auth --reset` |
| `~/.pi/agent/extensions/live/curated/nim.json` | Runtime NIM allowlist (seeded, then probe-merged via `pi-setup models probe --write`) |

## Maintenance

- **New Zen free model** → add the exact id to
  `dev/pi/extensions/live/curated/opencode.json`, then `make pi` — see the
  [Zen tradeoff](#zen-tradeoff--static-list-instead-of-probing).
- **NIM model list churn** → `pi-setup models probe --write` (re-probes and
  merges; hand-kept patterns survive).
- **Provider keys** → `pi-setup auth --force` re-prompts;
  `pi-setup auth --reset` wipes.
- **Extension changes** → edit `index.ts`, `tsc -p dev/pi --noEmit` (needs
  `npm install` in `dev/pi` first on a fresh machine), `make pi`, `/reload`.

## Tips

- Run `pi-setup auth` before `pi` so `/login` isn't needed for the 2
  providers.
- Default model `opencode/big-pickle` is adjustable via `/model`.
- `enableInstallTelemetry: false` is seeded; `enableAnalytics` stays off.

## Future enhancements

Planned but **not implemented**:

- **Probe operation logging** — record NIM probe runs (per-model status,
  latency, keyword-skips, merged counts) to a log file so a probe history can
  be reviewed after the fact instead of only a terminal summary.
- **Model blacklist** — a curated list of specific model ids to exclude from
  probe results (e.g. deprecated or undesirable models that still answer 200),
  consulted before writing `curated/nim.json`.
