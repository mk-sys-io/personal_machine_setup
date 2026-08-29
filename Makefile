include config.env

DEPLOY_DIR := $(HOME)/.config

.PHONY: dotfiles dev all clean-stale ask-serve

# cp -r only adds/overwrites — it never removes files that were deleted
# from the source tree. Over time, stale scripts and configs accumulate
# in the deploy dirs, causing confusion and potential runtime interference.
# This target diffs source vs dest and removes orphans before deploying.
clean-stale:
	@echo "=== Cleaning stale files ==="
	@for dir in waybar sway; do \
		find dotfiles/$$dir -type f -printf '%P\n' | sort > /tmp/src.txt; \
		find $(DEPLOY_DIR)/$$dir -type f -printf '%P\n' | sort > /tmp/dst.txt; \
		stale=$$(comm -23 /tmp/dst.txt /tmp/src.txt); \
		if [ -n "$$stale" ]; then \
			echo "$$stale" | while read f; do \
				echo "  removing $$dir/$$f"; \
				rm -f "$(DEPLOY_DIR)/$$dir/$$f"; \
			done; \
		fi; \
		rm -f /tmp/src.txt /tmp/dst.txt; \
	done
	@echo "  done."

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
	ln -sfn $(DEPLOY_DIR)/sway/gtklock $(DEPLOY_DIR)/gtklock
	# rofi/swaync live inside sway dir — symlink for default paths
	ln -sfn $(DEPLOY_DIR)/sway/rofi $(DEPLOY_DIR)/rofi
	ln -sfn $(DEPLOY_DIR)/sway/swaync $(DEPLOY_DIR)/swaync
	# app config dirs
	# gtklock excluded — deployed via symlinks
	# rofi/swaync excluded — deployed as part of sway
	for app in gtk-3.0 kitty sway waybar ranger fzf fastfetch systemd; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		find dotfiles/$$app -mindepth 1 -maxdepth 1 -not -name '.*' -not -name 'snippets.txt' \
			-exec cp -r {} $(DEPLOY_DIR)/$$app/ \;; \
	done
	# snippets.txt is user-editable (rofi "✏️ Edit" / $mod+Ctrl+e) —
	# seed on first deploy only; never overwrite accumulated entries
	@test -f $(DEPLOY_DIR)/sway/snippets.txt || cp dotfiles/sway/snippets.txt $(DEPLOY_DIR)/sway/snippets.txt
	# starship prompt (flat file in ~/.config/)
	cp dotfiles/starship.toml $(DEPLOY_DIR)/starship.toml
	# wayland-pipewire-idle-inhibit (audio-based idle inhibitor) config
	mkdir -p $(DEPLOY_DIR)/wayland-pipewire-idle-inhibit
	cp dotfiles/wayland-pipewire-idle-inhibit/config.toml $(DEPLOY_DIR)/wayland-pipewire-idle-inhibit/config.toml
	# mime associations
	cp dotfiles/mimeapps.list $(DEPLOY_DIR)/mimeapps.list
	# ensure scripts are executable (cp -r may not preserve +x)
	chmod +x $(DEPLOY_DIR)/sway/scripts/*
	chmod +x $(DEPLOY_DIR)/waybar/scripts/*
	# browsers (policy dirs)
	mkdir -p $(DEPLOY_DIR)/browsers
	cp -r dotfiles/browsers/* $(DEPLOY_DIR)/browsers/
	# waybar scripts (explicit — dotfiles/waybar/ only has scripts)
	mkdir -p $(DEPLOY_DIR)/waybar/scripts
	cp -r dotfiles/waybar/scripts/* $(DEPLOY_DIR)/waybar/scripts/
	# linux_setup config — for runtime scripts
	mkdir -p $(DEPLOY_DIR)/linux_setup
	cp config.env $(DEPLOY_DIR)/linux_setup/config.env
	# obsidian (custom vault path)
	mkdir -p $(OBSIDIAN_VAULT_PATH)/.obsidian
	cp dotfiles/obsidian/* $(OBSIDIAN_VAULT_PATH)/.obsidian/
	@echo "Dotfiles deployed."

dev:
	@echo "=== Dev ==="
	mkdir -p $(DEPLOY_DIR)/opencode $(DEPLOY_DIR)/zed $(DEPLOY_DIR)/ruff
	cp dev/github.env       $(DEPLOY_DIR)/github.env
	chmod 600               $(DEPLOY_DIR)/github.env
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
	# tools → ~/.local/bin/ (strip any extension so pi-setup.py → pi-setup;
	# NOT `pi` — that's the Pi agent binary, which ~/.local/bin would shadow)
	for script in tools/*; do \
		[ -f "$$script" ] || continue; \
		name=$$(basename "$$script"); \
		name=$${name%.*}; \
		mkdir -p $(HOME)/.local/bin; \
		cp "$$script" $(HOME)/.local/bin/"$$name"; \
		chmod 755 $(HOME)/.local/bin/"$$name"; \
	done
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

all: dotfiles dev

# Start/stop the local AI answer server (tools/ask serve on 127.0.0.1:8787).
# Backs the ai:/deep:/learn: search engines in LibreWolf.
ask-serve:
	@echo "Starting ask serve on http://127.0.0.1:8787 (Ctrl+C to stop)..."
	$(HOME)/.local/bin/ask serve
