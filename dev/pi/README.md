# Pi — 5 providers

Pi coding agent provisioning across **OpenRouter**, **NaraRouter**,
**OpenCode Zen**, **NVIDIA NIM**, and **Google AI Studio**.
Source here is not live — `make dev` deploys it.

## Components

| Component | Source | Deployed | Role |
|---|---|---|---|
| Live extension | `extensions/live/index.ts` | `~/.pi/agent/extensions/live/` | Registers all 5 providers (`opencode` overrides the built-in); fetches live catalogs, filters through curated files, persists + fallback |
| Curated files | probe/fetch-generated → `~/.pi/agent/extensions/live/curated/` | same tree | `{ "patterns": [...] }` allowlists written by `pi-setup probe` (live) / `pi-setup fetch` (lists) — never shipped from source |
| `pi-setup` | `tools/pi_setup/` (zipapp) | `~/.local/bin/pi-setup` | `auth` (keys + discover offer), `probe`/`fetch` (curated discovery), `clean`, `dir` |
| Settings seed | `settings.seed.json` | copied to `~/.pi/agent/settings.json` (only-if-absent) | Defaults, telemetry off |

## Key resolution

`auth.json` credential > env var (`OPENCODE_API_KEY` / `NVIDIA_NIM_API_KEY`)
> `models.json` provider apiKey. Stored credential wins over env var.

## Provisioning flow

```bash
make dev        # dev configs + Pi extension + settings seed
pi-setup auth   # prompt/validate the 5 API keys (additive, resume-safe)
                # then offers to probe/fetch providers and generate curated files
pi-setup fetch --all --write    # re-fetch both free-model lists (fast)
pi-setup probe --all --write    # re-probe opencode/nim/gemini catalogs (30+ min)
                                # single-provider: probe|fetch <provider> --write
pi              # pick model via /model (default: opencode/big-pickle)
```

## Docs

Full usage + architecture: [docs/PI.md](docs/PI.md).
