# Pi — OpenCode Zen + NVIDIA NIM

Pi coding agent provisioning with two providers: **OpenCode Zen** (free-only)
and **NVIDIA NIM** (credit-based). Source here is not live — `make dev`
deploys it.

## Components

| Component | Source | Deployed | Role |
|---|---|---|---|
| Live extension | `extensions/live/index.ts` | `~/.pi/agent/extensions/live/` | Overrides built-in `opencode` + adds `nim`; fetches live catalogs, filters through curated files, persists + 15s fallback |
| Curated files | probe-generated → `~/.pi/agent/extensions/live/curated/` | same tree | `{ "patterns": [...] }` allowlists written by `pi-setup` probing (never shipped from source) |
| `pi-setup` | `tools/pi-setup.py` | `~/.local/bin/pi-setup` | `pi-setup auth` (keys + probe offer), `pi-setup probe`, `pi-setup dir` |
| Settings seed | `settings.seed.json` | copied to `~/.pi/agent/settings.json` (only-if-absent) | Defaults, telemetry off |

## Key resolution

`auth.json` credential > env var (`OPENCODE_API_KEY` / `NVIDIA_NIM_API_KEY`)
> `models.json` provider apiKey. Stored credential wins over env var.

## Provisioning flow

```bash
make dev        # dev configs + Pi extension + settings seed
pi-setup auth   # prompt/validate the 7 provider keys (additive, resume-safe)
                # then offers to probe providers and generate curated files
pi-setup probe --write        # re-probe a provider's catalog on demand
pi              # pick model via /model (default: opencode/big-pickle)
```

## Docs

Full usage + architecture: [docs/PI.md](docs/PI.md).
