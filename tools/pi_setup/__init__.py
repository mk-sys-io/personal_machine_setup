"""pi-setup — Pi provisioning: provider credentials + model allowlist discovery.

Subcommands:
  pi-setup auth [--provider <id>...|--all]   copy vault → Pi auth.json, show
                                status, choose provider(s) to probe/fetch,
                                generate manifest
  pi-setup auth check           run 'pi auth check --provider <id>' per target
  pi-setup clear                one-pass wipe of Pi auth.json + models-store.json
                                (vault untouched — re-run 'pi-setup auth' to re-copy)

  pi-setup probe <provider>...|--all [--write]  live chat-probe; keep verified models
  pi-setup fetch <provider>...|--all [--write]  fetch free-model lists (no live check)
  pi-setup dir                    list curated files in the live curated dir

Credentials live in the shared vault (~/.config/provider-registry/vault.json,
managed by `provider-registry add`); `pi-setup auth` copies them into Pi's
auth.json (the deployment target). After copying, `pi-setup auth` offers to
discover models for the configured providers (probe or fetch, per provider)
and generate the curated allowlists the live extension filters against
(runtime: ~/.pi/agent/extensions/live/curated/). Probing matters: catalogs list
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
