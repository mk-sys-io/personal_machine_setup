include config.env

DEPLOY_DIR := $(HOME)/.config

.PHONY: dotfiles dev all clean-stale

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
	# set default theme symlink
	mkdir -p $(DEPLOY_DIR)/sway/themes
	ln -sfn themes/github_dark $(DEPLOY_DIR)/sway/current-theme
	# app config dirs
	# gtklock excluded — deployed via symlinks
	# rofi/swaync excluded — deployed as part of sway
	for app in gtk-3.0 kitty sway waybar ranger fzf fastfetch systemd; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		find dotfiles/$$app -maxdepth 1 -not -name '.*' -not -name 'snippets.txt' \
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
	# brave/firefox (policy dirs)
	mkdir -p $(DEPLOY_DIR)/brave
	cp -r dotfiles/brave/*   $(DEPLOY_DIR)/brave/
	# brave desktop entry — force Dark via --force-dark-mode (follow device is broken on Sway)
	mkdir -p $(HOME)/.local/share/applications
	cp /usr/share/applications/brave-browser.desktop $(HOME)/.local/share/applications/brave-browser.desktop
	sed -i 's|Exec=\(/usr/bin/brave-browser-stable\)|Exec=\1 --force-dark-mode|g' $(HOME)/.local/share/applications/brave-browser.desktop
	mkdir -p $(DEPLOY_DIR)/firefox
	cp -r dotfiles/firefox/* $(DEPLOY_DIR)/firefox/
	# waybar scripts (explicit — dotfiles/waybar/ only has scripts)
	mkdir -p $(DEPLOY_DIR)/waybar/scripts
	cp -r dotfiles/waybar/scripts/* $(DEPLOY_DIR)/waybar/scripts/
	# linux_setup config (weather fallback, etc.) — for runtime scripts
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
	# tools → ~/.local/bin/
	for script in tools/*; do \
		name=$$(basename "$$script" .sh); \
		mkdir -p $(HOME)/.local/bin; \
		cp "$$script" $(HOME)/.local/bin/"$$name"; \
		chmod 755 $(HOME)/.local/bin/"$$name"; \
	done
	@echo "Dev configs deployed."

all: dotfiles dev
