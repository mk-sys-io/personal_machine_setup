# AGENTS.md — linux_setup

Mirror of the live workstation (Sway/Wayland + Ark lockdown).
Changes here become live — edit here, deploy, no separate source.

| Component → start |
| --- |
| `dotfiles/` → desktop env, `make dotfiles` (runs `clean-stale` first) → `~/.config/` |
| `dev/` + `tools/` → tooling, `make dev` / `make tools` → `~/.config/`, `~/.local/bin/` |
| `lib/` → system modules (`NN-name.sh/.py`), via `./install.py` or singly `bash lib/NN-x.sh` (0=pass/1=fail/2=skip/3=partial) |
| Ark (`ark/`, `etc/ark/`) → lockdown, `./install.py --only 60,61` → `/opt/ark`, runtime truth |
| `scout/` → free-model facade, `make scout` (manual; not in install.py) → `~/.config/scout/`, `~/.local/bin/scout`, user units; runbook → `scout/docs/operating.md` |
| Why / parked → `docs/adr/README.md`, `docs/backlog/backlog.md` |
| TS/JS lint · format → `docs/typescript-tooling.md` |
| Agent harness / modes / feedback → `docs/harness-design.md` |

## Session-start

- `git status --short` + `git log --oneline -5` — what changed, where you are.
- `git branch --show-current` — feature branches only.
- scout guard: `systemctl --user status scout.timer` (must be armed) + `tail -n1 "$(ls -t ~/.local/state/scout/report-*.md | head -1)"` (last line must start `verdict: `).
- Table above routes everything else; `docs/` over memory.

## Verify (no tests)

- `ruff check --fix <file>`, `basedpyright <file>`, `bash -n` + `shellcheck`.
- `uvx --quiet vulture --min-confidence 100 .` on demand (exit 3 = findings).
- Error → fix now; warning → fix or note trade-off; info/hint → tolerate.
