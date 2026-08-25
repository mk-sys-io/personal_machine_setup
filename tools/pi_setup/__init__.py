"""pi-setup — Pi provisioning: provider credentials + model allowlist discovery.

Subcommands:
  pi-setup auth                 prompt for missing providers (additive)
  pi-setup auth --provider <id>...    restrict to listed providers (repeatable)
  pi-setup auth --force         re-prompt even if already configured
  pi-setup auth --reset [--yes]       back up + wipe ALL credentials, re-prompt
  pi-setup auth --reset --provider <id>...   remove + re-prompt only those
  pi-setup auth check           run 'pi auth check --provider <id>' per target
  pi-setup clean [--yes]        back up + wipe credentials AND cached model
                                catalogs (auth.json + models-store.json); no
                                re-prompting — follow with 'pi-setup auth'

  pi-setup probe <provider>...|--all [--write]  live chat-probe; keep verified models
  pi-setup fetch <provider>...|--all [--write]  fetch free-model lists (no live check)
  pi-setup dir                    list curated files in the live curated dir

After credential setup, `pi-setup auth` offers to discover models for the
configured providers (probe or fetch, per provider) and generate the curated
allowlists the live extension filters against (runtime:
~/.pi/agent/extensions/live/curated/). Probing matters: catalogs list
stale/retired models and paid tiers — a Pi-like chat request (tool-calling +
streaming) is the only reliable availability/freeness check; gemini is probed
through Google's native :generateContent protocol, the same path Pi's
extension uses. Two verbs, two trust levels: `probe` sends a real chat request
per model (opencode/nim/gemini); `fetch` downloads a provider's free-model
list (openrouter/nararouter) and verifies nothing.

Decision record — Zen probing (2026-08-24): all five providers are probed or
fetched dynamically; there is no static curated seed in the repo. Zen was
previously static because Cloudflare blocked urllib UAs on /chat/completions
and free-tier first-token latency hit 20 s+. Both verified gone/measurable:
urllib passes Cloudflare, and a 45 s per-model bound covers the slowest
observed first token (nemotron-3-ultra-free at 25.5 s TTFB). Freeness is
classified by status alone (see classify_zen): Zen checks billing BEFORE
upstream dispatch, so with a deliberately credit-free account every paid
model answers 401/402 CreditsError — a probe can never mistake paid for free.
200 keeps; 429 FreeUsageLimitError keeps (free-tier rate limit, still proof
of free membership); everything else drops.

Catalog/auth GETs retry up to FETCH_RETRIES with backoff on transient network
errors and send an explicit User-Agent; HTTP-level rejections are reported as
their status code, never as "offline".

Env overrides (dev/test):
  PI_CURATED_DIR   curated dir (default: ~/.pi/agent/extensions/live/curated)
  PI_AUTH_JSON     auth.json (default: ~/.pi/agent/auth.json)
  PI_STORE_JSON    models-store.json (default: ~/.pi/agent/models-store.json)

Deployed to ~/.local/bin/pi-setup by `make dev` (zipapp bundle of this
package). This directory is the source of truth — never edit the deployed
artifact.
"""
