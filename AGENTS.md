# AGENTS.md — linux_setup

## Principles

- This repo is a mirror of the live workstation (Sway/Wayland + Ark lockdown): edit here, deploy, no separate source. Never edit deployed copies under `~/.config/` directly.
- Scripts in `tools/` are run manually by the operator — do not execute them as part of verification.
- TEMPORARY: no test suite exists; do not scaffold tests or claim test failures. Verify via lint skills, `bash -n` + `shellcheck` for shell, and targeted command runs. Remove this bullet once tests are added.
- TS/JS lint/format tooling reference: `docs/typescript-tooling.md`. Web research rules live in the global AGENTS.md loaded alongside this file.

| Component → start | Command |
| --- | --- |
| `dotfiles/` → desktop env | `make dotfiles` (runs `clean-stale` first) → `~/.config/` |
| `dev/` + `tools/` → tooling | `make dev` / `make tools` → `~/.config/`, `~/.local/bin/` |
| `dev/harness/` → shared agent assets (`AGENTS.md`, `skills/`) | `make dev` → `~/.config/opencode/AGENTS.md`, `~/.pi/agent/AGENTS.md`, `~/.agents/skills/` |
| `dev/pi/` → Pi agent | `make dev` → `~/.pi/agent/` |
| `lib/` → system modules (`NN-name.sh/.py`); exit codes 0=pass 1=fail 2=skip 3=partial | `./install.py` or singly `bash lib/NN-x.sh` |
| Ark lockdown (`ark/`, `etc/ark/`) | `./install.py --only 60,61` → `/opt/ark` |
| `scout/` → free-model facade (manual, not in install.py) | `make scout` → `~/.config/scout/`, `~/.local/bin/scout`, user units; runbook `scout/docs/operating.md` |
| Android debloat (operator-only, by hand) | `docs/android-debloat-runbook.md` |
| Why / parked | `docs/adr/README.md`, `docs/backlog/backlog.md` |
| Agent harness / modes / feedback | `docs/harness-design.md` |

## Session-start

- `git status --short` + `git log --oneline -5` — what changed, where you are.
- `git branch --show-current` — feature branches only.
- scout guard: `systemctl --user status scout.timer` (must be armed) + `tail -n1 "$(ls -t ~/.local/state/scout/report-*.md | head -1)"` (last line must start `verdict:`).
- Table above routes everything else; `docs/` over memory.
