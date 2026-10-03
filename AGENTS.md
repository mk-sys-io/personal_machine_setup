# AGENTS.md — linux_setup

Mirror of the live workstation (Sway/Wayland + Ark lockdown).
Changes here become live — edit here, deploy, no separate source.

| Component → start |
| --- |
| `dotfiles/` → desktop env, `make dotfiles` (runs `clean-stale` first) → `~/.config/` |
| `dev/` + `tools/` → tooling, `make dev` / `make tools` → `~/.config/`, `~/.local/bin/`, `~/.pi/agent/` |
| `lib/` → system modules (`NN-name.sh/.py`), via `./install.py` or singly `bash lib/NN-x.sh` (0=pass/1=fail/2=skip/3=partial) |
| Ark (`ark/`, `etc/ark/`) → lockdown, `sudo bash lib/60-ark.sh` → `/opt/ark`, runtime truth |
| Why / parked → `docs/adr/README.md`, `docs/backlog.md` |

## Session-start

- `git status --short` + `git log --oneline -5` — what changed, where you are.
- `git branch --show-current` — feature branches only.
- Table above routes everything else; `docs/` over memory.

## Verify (no tests)

- `ruff check --fix <file>`, `basedpyright <file>`, `bash -n` + `shellcheck`.
- `uvx --quiet vulture --min-confidence 100 .` on demand (exit 3 = findings).
- Error → fix now; warning → fix or note trade-off; info/hint → tolerate.
