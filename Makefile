# CONTRACT: sources = dotfiles/ + dev/ + tools/ + top-level config.txt.
# Nothing else in the repo is read. Run from the repo root (`make -C
# <root>` from elsewhere is equivalent); any other cwd fails loud at
# the config.txt guard below — relative source paths and
# REPO_ROOT=$(CURDIR) are only correct at the root.
# Deploy model: `dotfiles` (desktop configs, gated by `clean-stale`),
# `dev` (dev configs + Pi agent seed), `tools` (binaries + libs);
# `all` runs all three. No inter-target deps — each target is standalone.
# Sweep manifest lives in lib/helpers/clean_stale.sh (explicit
# sweep_exclusive/sweep_additive pairs, repo-is-truth, per-app subdir
# scope): unknown = dest file not in `git ls-files <src>` for an
# exclusive pair. Usage: make dotfiles/dev/tools/all/clean-stale;
# YES=1 only (scripts/CI, incl. no-TTY runs).
-include config.txt
ifeq ($(wildcard config.txt),)
$(error config.txt missing — run from the repo root (or make -C <root>); fresh clone? try: git pull && ls config.txt)
endif

DEPLOY_DIR := $(HOME)/.config

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DELETE_ON_ERROR:

.PHONY: dotfiles dev tools all clean-stale

# SCOPE: managed config dirs only — never $HOME top-level.
# Skeleton dirs (Downloads/Documents/Music/Pictures/Videos, owned by
# 15-home-skeleton) are outside clean-stale by design; do not add $HOME
# entries here.
# Single prompt gate: lists orphans, strict `y` deletes + exit 0 (deploy
# continues in the same run), anything else exit 1 (aborts `dotfiles`).
clean-stale:
	YES="$(YES)" bash lib/helpers/clean_stale.sh

dotfiles: clean-stale
	@echo "=== Dotfiles ==="
	# bashrc — managed content in a dedicated file, sourced from the original
	cp dotfiles/bashrc $(HOME)/.config/bashrc
	@if ! grep -qF 'source ~/.config/bashrc' $(HOME)/.bashrc 2>/dev/null; then \
		echo '[ -f ~/.config/bashrc ] && source ~/.config/bashrc' >> $(HOME)/.bashrc; \
		echo "bashrc: added source line to ~/.bashrc"; \
	fi
	# symlinks for apps that expect default locations (create BEFORE app loop)
	mkdir -p $(DEPLOY_DIR)/sway/gtklock
	ln -sfnT "$(DEPLOY_DIR)/sway/gtklock" "$(DEPLOY_DIR)/gtklock"
	# rofi/swaync live inside sway dir — symlink for default paths
	ln -sfnT "$(DEPLOY_DIR)/sway/rofi" "$(DEPLOY_DIR)/rofi"
	ln -sfnT "$(DEPLOY_DIR)/sway/swaync" "$(DEPLOY_DIR)/swaync"
	# app config dirs
	# gtklock excluded — deployed via symlinks
	# rofi/swaync excluded — deployed as part of sway
	for app in clipse gtk-3.0 gtk-4.0 kitty mpv sway waybar yazi fzf fastfetch systemd; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		find dotfiles/$$app -mindepth 1 -maxdepth 1 -not -name '.*' \
			-exec cp -r {} $(DEPLOY_DIR)/$$app/ \;; \
	done
	# espanso — copy only on content change so the watcher doesn't restart
	# on every `make` (cp -r updates mtime even when identical, triggering
	# auto_restart + notification + Wayland detect-window flash).
	for f in $$(find dotfiles/espanso -type f -not -name '.*'); do \
		dst="$(DEPLOY_DIR)/$${f#dotfiles/}"; \
		mkdir -p "$$(dirname "$$dst")"; \
		cmp -s "$$f" "$$dst" || cp "$$f" "$$dst"; \
	done
	# starship prompt (flat file in ~/.config/)
	cp dotfiles/starship.toml $(DEPLOY_DIR)/starship.toml
	# GTK2 theme (flat file in ~/)
	cp dotfiles/gtkrc-2.0 $(HOME)/.gtkrc-2.0
	# wayland-pipewire-idle-inhibit (audio-based idle inhibitor) config
	mkdir -p $(DEPLOY_DIR)/wayland-pipewire-idle-inhibit
	cp dotfiles/wayland-pipewire-idle-inhibit/config.toml $(DEPLOY_DIR)/wayland-pipewire-idle-inhibit/config.toml
	# mime associations
	cp dotfiles/mimeapps.list $(DEPLOY_DIR)/mimeapps.list
	# xdg user dirs (pinned file owned by 15-home-skeleton §15.2)
	cp dotfiles/xdg/user-dirs.dirs $(DEPLOY_DIR)/user-dirs.dirs
	# environment.d (30-C)
	mkdir -p $(DEPLOY_DIR)/environment.d
	cp dotfiles/environment.d/xdg.conf $(DEPLOY_DIR)/environment.d/xdg.conf
	# ensure scripts are executable (cp -r may not preserve +x)
	[ -d "$(DEPLOY_DIR)/sway/scripts" ] && chmod +x "$(DEPLOY_DIR)/sway/scripts"/*
	[ -d "$(DEPLOY_DIR)/waybar/scripts" ] && chmod +x "$(DEPLOY_DIR)/waybar/scripts"/*
	# browsers (policy dirs)
	mkdir -p "$(DEPLOY_DIR)/browsers"
	[ -d "dotfiles/browsers" ] && cp -r dotfiles/browsers/* "$(DEPLOY_DIR)/browsers/"
	# linux_setup config — for runtime scripts (REPO_ROOT derived at
	# deploy time, never stored in the committed config.txt per 12 §12.3)
	mkdir -p $(DEPLOY_DIR)/linux_setup
	cp config.txt $(DEPLOY_DIR)/linux_setup/config.txt
	echo "REPO_ROOT=$(CURDIR)" >> $(DEPLOY_DIR)/linux_setup/config.txt
	# obsidian (custom vault path)
	@[ -n "$(OBSIDIAN_VAULT_PATH)" ] || { echo "OBSIDIAN_VAULT_PATH empty, aborting"; exit 1; }
	mkdir -p "$(OBSIDIAN_VAULT_PATH)/.obsidian"
	[ -d "dotfiles/obsidian" ] && cp dotfiles/obsidian/* "$(OBSIDIAN_VAULT_PATH)/.obsidian/"
	@echo "Dotfiles deployed."

dev:
	@echo "=== Dev ==="
	mkdir -p $(DEPLOY_DIR)/opencode $(DEPLOY_DIR)/zed $(DEPLOY_DIR)/ruff
	# Exclude docs/ (dev reference), README.md (build-time changes only),
	# and typecheck-only scaffolding (tsconfig.json + types/) from deployment
	# find lists only top-level entries — cp -r handles recursive copy into dest
	find dev/opencode -mindepth 1 -maxdepth 1 \
		-not -name 'docs' -not -name 'README.md' \
		-not -name 'tsconfig.json' -not -name 'types' \
		-exec cp -r {} $(DEPLOY_DIR)/opencode/ \;
	cp dev/zed/*            $(DEPLOY_DIR)/zed/
	# ruff global config → ~/.config/ruff/
	cp dev/ruff/pyproject.toml $(DEPLOY_DIR)/ruff/pyproject.toml
	# shellcheck global config → ~/.shellcheckrc (HOME wins over XDG)
	cp dev/shellcheck/.shellcheckrc $(HOME)/.shellcheckrc
	# --- Pi agent (~/.pi/agent) ---
	@echo "=== Pi ==="
	# extension source -> ~/.pi/agent/extensions (no curated seeds ship in
	# the repo; runtime allowlists are written by pi-setup into the runtime
	# curated dir, which this copy never deletes or overwrites)
	mkdir -p $(HOME)/.pi/agent/extensions
	@cd dev/pi/extensions && find . -type f \
		-exec cp --parents {} $(HOME)/.pi/agent/extensions/ \;
	# settings seed -> ~/.pi/agent/settings.json (only-if-absent, preserves user edits)
	@test -f $(HOME)/.pi/agent/settings.json || \
		cp dev/pi/settings.seed.json $(HOME)/.pi/agent/settings.json
	@echo "Pi deployed."
	@echo "Dev configs deployed."

tools:
	@echo "=== Tools ==="
	# tools → ~/.local/bin/ (strip any extension so pi-setup.py → pi-setup;
	# NOT `pi` — that's the Pi agent binary, which ~/.local/bin would shadow)
	# Deleting a tool? Its ~/.local/bin name is swept by clean-stale
	# (history-derived); manual rm not needed.
	for script in tools/*; do \
		[ -f "$$script" ] || continue; \
		[ "$$script" = "tools/init.sh" ] && continue; \
		name=$$(basename "$$script"); \
		name=$${name%.*}; \
		mkdir -p $(HOME)/.local/bin; \
		cp "$$script" $(HOME)/.local/bin/"$$name"; \
		chmod 755 $(HOME)/.local/bin/"$$name"; \
	done
	# init: rendered single-file deploy (mtime-safe). The loop above skips
	# init.sh; this step injects dev/git/gitleaks.toml at the marker and
	# installs only on content change, so plain `make dev` runs never touch
	# ~/.local/bin/init needlessly. Fails loud if the marker is missing.
	python3 -c "from pathlib import Path; live=Path('$(HOME)/.local/bin/init'); src=Path('tools/init.sh').read_text(); m='#__GITLEAKS_TOML__'; assert src.count(m)==1,'init marker count != 1'; new=src.replace(m,Path('dev/git/gitleaks.toml').read_text().rstrip(chr(10))); cur=live.read_text() if live.exists() else ''; [live.write_text(new) if new!=cur else None]"; chmod 755 $(HOME)/.local/bin/init
	# provider_registry library -> user site-packages (no pip, no PEP 668 —
	# pip install --user is blocked on this Debian trixie system). User site
	# is auto-on sys.path, so ask.py/pi_setup import it with no path hacking.
	# Snapshot copy (cp -r), NOT a .pth/editable link: the repo is the only
	# source of truth and re-deploy after a change is the intended effect —
	# edits under tools/ do nothing until `make dev`. Resolve the user
	# site-packages path dynamically (never hard-code a Python minor version)
	# so a system Python bump doesn't silently break importers. The find
	# cleanup mirrors the zipapp staging below so cp -r doesn't leak
	# __pycache__ into site-packages (stale .pyc from another Python minor).
	USER_SITE=$$(python3 -m site --user-site) && \
	mkdir -p $$USER_SITE && \
	cp -r tools/provider_registry $$USER_SITE/ && \
	find $$USER_SITE/provider_registry \
		-name '__pycache__' -type d -exec rm -rf {} +
	# pi-setup: bundle tools/pi_setup/ -> one executable zipapp. The loop
	# above skips directories, so the package is deployed only via this step.
	# Staging adds an absolute-import bootstrap __main__.py beside the package
	# (zipapp runs archive-root __main__.py as a plain script, where relative
	# imports would fail).
	tmp=$$(mktemp -d); \
	mkdir -p $$tmp/root && \
	cp -r tools/pi_setup $$tmp/root/pi_setup && \
	find $$tmp/root -name '__pycache__' -type d -exec rm -rf {} + ; \
	printf 'import sys\n\nfrom pi_setup.cli import main\n\nsys.exit(main(sys.argv[1:]))\n' > $$tmp/root/__main__.py && \
	python3 -m zipapp -o $(HOME)/.local/bin/pi-setup -p '/usr/bin/env python3' $$tmp/root && \
	chmod 755 $(HOME)/.local/bin/pi-setup; \
	rm -rf $$tmp
	@echo "Tools deployed."

all: dotfiles dev tools
