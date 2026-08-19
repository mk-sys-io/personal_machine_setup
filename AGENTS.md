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
  `~/.local/bin/<name sans extension>` (strip-any-extension loop, so
  `pi-setup.py` → `pi-setup`). `pi-setup` is Python (stdlib-only, typed; ruff
  + basedpyright on the new file).
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
- Rationale: `docs/RESTRICTION_POLICY.md`, `docs/KNOWN_ISSUES.md`, `docs/NVIDIA.md`.

## Pi provisioning

- `make pi` (standalone, idempotent) deploys the live extension + settings seed
  to `~/.pi/agent/`. Curated files: only `opencode.json` (Zen
  static list) is always copied; all other curated files (NIM, OpenRouter,
  Z.ai, NaraRouter, Google AI Studio) are generated at runtime by `pi-setup probe --write`.
- `pi-setup` (from `tools/pi-setup.py` → `~/.local/bin/pi-setup`) does
  credentials + model discovery for 6 API-key providers: `pi-setup auth`
  prompts + live-validates keys for opencode, nim, openrouter, zai, nararouter,
  gemini into `~/.pi/agent/auth.json` — additive by default, `--reset`
  for a clean slate, `check` verifies. After setup it offers to probe NIM + fetch free-model lists;
  `pi-setup probe [--write]` writes fresh curated files each time
  (no merge with previous results), `dir` lists curated files.
- NOTE: the tool is named `pi-setup` deliberately — `pi` is the Pi agent
  binary (npm global), and `~/.local/bin` outranks `/usr/bin` in PATH, so a
  `pi` binary here would shadow the agent. `pi-setup auth check` shells out to
  the real `pi auth check`.
- Key resolution: stored `auth.json` credential > env var (`OPENCODE_API_KEY`
  / `NVIDIA_NIM_API_KEY` / etc.) > `models.json` provider apiKey.
- NIM probing sends a minimal 25 s-bounded chat request per chat-eligible model
  (non-chat ids keyword-pre-filtered, 1.5 s pacing between probes to avoid NIM
   worker saturation); only fast models are written to `curated/nim.json`.
  A preflight connectivity check bails early on network failure.
  OpenRouter/Z.ai/NaraRouter use auto-fetched free-model lists (no probing).
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
