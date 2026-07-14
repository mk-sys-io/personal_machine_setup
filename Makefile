include config.env

DEPLOY_DIR := $(HOME)/.config
SUBST := sed -i 's|@USERNAME@|$(USERNAME)|g; s|@OPENCODE_PATH@|$(OPENCODE_PATH)|g; s|@OBSIDIAN_VAULT_PATH@|$(OBSIDIAN_VAULT_PATH)|g'

.PHONY: dotfiles dev all

dotfiles:
	@echo "=== Dotfiles ==="
	# bashrc
	cp dotfiles/bashrc $(HOME)/.bashrc
	$(SUBST) $(HOME)/.bashrc
	# symlinks for apps that expect default locations (create BEFORE app loop)
	mkdir -p $(DEPLOY_DIR)/sway/foot $(DEPLOY_DIR)/sway/gtklock
	ln -sfn $(DEPLOY_DIR)/sway/foot $(DEPLOY_DIR)/foot
	ln -sfn $(DEPLOY_DIR)/sway/gtklock $(DEPLOY_DIR)/gtklock
	# rofi/swaync live inside sway dir — symlink for default paths
	ln -sfn $(DEPLOY_DIR)/sway/rofi $(DEPLOY_DIR)/rofi
	ln -sfn $(DEPLOY_DIR)/sway/swaync $(DEPLOY_DIR)/swaync
	# set default theme symlink
	mkdir -p $(DEPLOY_DIR)/sway/themes
	ln -sfn themes/github_dark $(DEPLOY_DIR)/sway/current-theme
	# app config dirs
	# foot/gtklock excluded — deployed via symlinks
	# rofi/swaync excluded — deployed as part of sway
	for app in kitty sway waybar ranger fzf; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		cp -r dotfiles/$$app/* $(DEPLOY_DIR)/$$app/; \
	done
	# ensure scripts are executable (cp -r may not preserve +x)
	chmod +x $(DEPLOY_DIR)/sway/scripts/*
	chmod +x $(DEPLOY_DIR)/waybar/scripts/*
	# brave/firefox (policy dirs)
	mkdir -p $(DEPLOY_DIR)/brave
	cp -r dotfiles/brave/*   $(DEPLOY_DIR)/brave/
	mkdir -p $(DEPLOY_DIR)/firefox
	cp -r dotfiles/firefox/* $(DEPLOY_DIR)/firefox/
	# waybar scripts (explicit — dotfiles/waybar/ only has scripts)
	mkdir -p $(DEPLOY_DIR)/waybar/scripts
	cp -r dotfiles/waybar/scripts/* $(DEPLOY_DIR)/waybar/scripts/
	# obsidian (custom vault path)
	mkdir -p $(OBSIDIAN_VAULT_PATH)/.obsidian
	cp dotfiles/obsidian/* $(OBSIDIAN_VAULT_PATH)/.obsidian/
	@echo "Dotfiles deployed."

dev:
	@echo "=== Dev ==="
	mkdir -p $(DEPLOY_DIR)/opencode $(DEPLOY_DIR)/container $(DEPLOY_DIR)/zed
	cp dev/github.env       $(DEPLOY_DIR)/github.env
	chmod 600               $(DEPLOY_DIR)/github.env
	cp dev/opencode/*       $(DEPLOY_DIR)/opencode/
	cp dev/zed/*            $(DEPLOY_DIR)/zed/
	@echo "Dev configs deployed."

all: dotfiles dev
