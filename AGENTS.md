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
  `~/.local/bin/<name sans .sh>`.
- `lib/` modules run via `./install.sh` (00-checks → 60-ark) or singly
  (`bash lib/NN-x.sh`). Exit codes: 0=pass/1=fail/2=skip/3=partial. Modules use
  `set -euo pipefail`; `install.sh` doesn't. `60-ark.sh` needs root.
- Packages: add a line to the right `packages/*.txt` (format header in each
  file), verify with `bash lib/20-packages.sh`.

## Ark system

- `ark/scripts/*.py` + `etc/ark/*` are gomplate templates (`{{ .Env.X }}`,
  rendered from `config.env`). Never run them from the repo — `sudo bash
  lib/60-ark.sh` deploys to `/opt/ark` + `/usr/local/{bin,lib}/ark`, the runtime
  truth. Re-run after any edit there.
- Modes `unrestricted`/`focused`/`locked` live in `/opt/ark/mode`; `ark lock`
  drops sudo group (recover: `ark revert`, timeshift). Cask = timelock creds
  (`mcask`/`uncask`); `chattr` only via `immutable_lib.py`; blocklist data in
  `/opt/ark/domains/`.
- Rationale: `docs/RESTRICTION_POLICY.md`, `docs/KNOWN_ISSUES.md`, `docs/NVIDIA.md`.

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
