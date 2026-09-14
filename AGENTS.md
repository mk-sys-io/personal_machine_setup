# AGENTS.md — linux_setup

Provisioning + dotfiles for a Sway/Wayland workstation with the Ark lockdown
system (DNS/firewall allowlist, timelock-casked creds). Source is not live:
edits under `dotfiles/`/`dev/` do nothing until deployed.

## Deployment model

- `make all` = dotfiles + dev, deployed to `~/.config/`. `make dotfiles` first
  runs `clean-stale` (removes orphans in `~/.config/{sway,waybar}`). `system/`
  feeds `lib/40-system_config.sh`.
- `make dev` copies `dev/opencode/` → global `~/.config/opencode/` (excluding
  its `docs/`, `README.md`, `tsconfig.json`, `types/`) — this is the *global*
  OpenCode config; editing it affects every repo. Also deploys `dev/ruff/` →
  `~/.config/ruff/`, `dev/shellcheck/` → `~/.shellcheckrc`, `tools/*` →
  `~/.local/bin/<name sans extension>` (strip-any-extension loop; directories
  skipped). Exception: `pi-setup` is the zipapp bundle of the `tools/pi_setup/`
  package (stdlib-only, typed; ruff + basedpyright), built by a dedicated
  `python3 -m zipapp` step in the `dev` target.
- `lib/` modules run via `./install.sh` (00-checks → 60-ark) or singly
  (`bash lib/NN-x.sh`). Exit codes: 0=pass/1=fail/2=skip/3=partial. Modules use
  `set -euo pipefail`; `install.sh` doesn't. `60-ark.sh` needs root.
- Packages: add a line to the right `packages/*.txt` (format header in each
  file), verify with `bash lib/20-packages.sh`.

## Ark system

- `ark/scripts/ark/` — main CLI package (`enable`/`disable`/`status`/`abort`/`logs`).
  `ark/scripts/cask/` — cask subsystem (`lib`, `system`, `mcask`, `uncask`, `clipboard`).
  `ark/scripts/ark.py` is a thin entry-point shim; `immutable_lib.py` and
  `mode.py` stay top-level. `etc/ark/*` are gomplate templates
  (`{{ .Env.X }}`, rendered from `config.env`). Never run them from the
  repo — `sudo bash lib/60-ark.sh` deploys to `/opt/ark` + `/usr/local/bin`,
  the runtime truth. Re-run after any edit there.
- `deploy_blocklist` always overwrites the live domain files (`sources.json`,
  `blocklist-custom.txt`, `blocklist-exceptions.txt`, `blocklist-exclude.txt`)
  from the repo — repo is the source of truth; a missing repo file aborts the
  deploy. Live copies are deployed root:root (custom 640, others 644) and netmgr
  enforces root ownership on writes, so non-root users can't modify the lists.
- Modes `unrestricted`/`focused`/`locked` live in `/opt/ark/mode`; `ark lock`
  drops sudo group (recover: `ark revert`, timeshift). Cask = timelock creds
  (`mcask`/`uncask`); `chattr` only via `immutable_lib.py`; blocklist data in
  `/opt/ark/domains/`.
- Rationale: `docs/restriction-policy.md`, `docs/nvidia.md`.

## API-key store (gopass)

- gopass is the single API-key store for the 5 providers (openrouter,
  nararouter, opencode, nim, gemini). Entries live under
  `provider-registry/<provider>` (env `PI_GOPASS_PREFIX`, default
  `provider-registry`); the API key is the `key` field, an optional
  `strategy` field overrides probe/fetch discovery per provider.
- Add a key: `gopass insert provider-registry/<provider> key` (then
  `gopass sync` if the store is remote). Consumers resolve keys via
  `gopass show` — no plaintext vault, no plaintext on disk.
- `tools/provider_registry/` is the shared module: provider identity,
  endpoints, chat contract (`chat.py`), and gopass key resolution
  (`config.py::gopass_show_field`). Exit-code mapping: 6=NotInitialized (store not
  initialized), 10=NotFound (missing entry → "run: gopass insert ..."),
  11=Decrypt / 12=Encrypt / 13=List / 18=IO → ToolError, 3=Aborted.
  `gopass` binary resolved via `PI_GOPASS_BIN` (default `gopass`).
- `tools/pi_setup/` is Pi-specific: probe/fetch discovery, curated files,
  manifest generation, and `pi-setup auth` (copies gopass creds →
  `~/.pi/agent/auth.json`). Pi's runtime key resolution (auth.json > env
  var > models.json) is unchanged.
- Communication patterns (per-request read / copy-to-location / `gopass env`)
  and the FM1–FM7 failure modes: `docs/gopass.md`.

## Pi provisioning

- `make dev` also deploys the Pi live extension + settings seed (idempotent;
  part of `make all`) to `~/.pi/agent/`. Curated files: NONE ship in the
  repo — all five (opencode, nim, openrouter-free, nararouter-free, gemini)
  are generated at runtime (`pi-setup probe <provider>...|--all --write` for
  opencode/nim/gemini, `pi-setup fetch <provider>...|--all --write` for
  openrouter/nararouter).
- `pi-setup` (zipapp bundle of `tools/pi_setup/` → `~/.local/bin/pi-setup`) does
  credentials + model discovery for 5 API-key providers: `pi-setup auth`
  prompts + live-validates keys for openrouter, nararouter, opencode,
  nim, gemini into `~/.pi/agent/auth.json` — additive by default, `--reset`
  wipes credentials and re-prompts (`--reset --provider X...` removes +
  re-prompts only those), `check` verifies; `--provider` is an auth-only
  flag. Flag rules (fail fast): `check`
  rejects `--reset`, `--reset` rejects `--force`, `--yes` requires `--reset`.
  `pi-setup clean [--yes]` wipes auth.json AND purges models-store.json
  (cached catalogs; stale entries resurrect unfiltered catalogs on failed
  refresh) — scripted complement to Pi's interactive per-provider `/logout`.
  After setup it offers to probe/fetch each configured provider;
  `pi-setup probe <provider>...|--all [--write]` (live chat-probe:
  opencode/nim/gemini) and `pi-setup fetch <provider>...|--all [--write]`
  (free-list download: openrouter/nararouter) write fresh curated files
  each time (no merge with previous results); multi-provider runs continue
  past individual failures (exit 1 if any failed); `dir` lists curated
  files.
- NOTE: the tool is named `pi-setup` deliberately — `pi` is the Pi agent
  binary (npm global), and `~/.local/bin` outranks `/usr/bin` in PATH, so a
  `pi` binary here would shadow the agent. `pi-setup auth check` shells out to
  the real `pi auth check`.
- Key resolution (Pi runtime, unchanged): stored `auth.json` credential >
  env var (`OPENCODE_API_KEY` / `NVIDIA_NIM_API_KEY` / etc.) > `models.json`
  provider apiKey. `auth.json` is fed from the gopass store by `pi-setup auth`.
- NIM probing sends a minimal 25 s-bounded chat request per chat-eligible model
  (non-chat ids keyword-pre-filtered, 1.5 s pacing between probes to avoid NIM
   worker saturation); only fast models are written to `curated/nim.json`.
  A preflight connectivity check bails early on network failure.
  OpenRouter/NaraRouter use free-model lists downloaded by
  `pi-setup fetch` (one GET, no live verification).
  Google AI Studio probes the native catalog with 3-layer filtering
  (supportedGenerationMethods → NON_CHAT_KEYWORDS → live chat probe).
- Dev note: the Pi extension is TypeScript — a fresh machine runs `npm install`
  in `dev/pi` before `tsc -p dev/pi --noEmit` (dev-only; runtime loads `.ts`
  via jiti, no build step).
- Full guide: `dev/pi/docs/PI.md`.

## Portability

`AGENTS.md` + committed `docs/` are the portable source of truth. Basic Memory
(`~/basic-memory/`, not git-tracked) and `plans/` (globally git-ignored) are
machine-local — absent on a fresh machine; nothing operationally necessary may
live only there.

## Required (gitignored — never commit)

`config.env` (needed by `Makefile` + modules; generate via
`tools/bootstrap-config.sh`) and `dev/github.env`. Global pre-commit hook runs
`gitleaks protect --staged`.

## Lint / typecheck (no tests)

`ruff check --fix` + `basedpyright` (pyrightconfig: extraPaths lib/python +
ark/scripts, standard); `bash -n` + `shellcheck` (SC1090/1091/2154 disabled
globally).
