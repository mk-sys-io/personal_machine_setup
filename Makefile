include config.env

DEPLOY_DIR := $(HOME)/.config
SUBST := sed -i 's|@USERNAME@|$(USERNAME)|g; s|@OPENCODE_PATH@|$(OPENCODE_PATH)|g; s|@OBSIDIAN_VAULT_PATH@|$(OBSIDIAN_VAULT_PATH)|g; s|@LOCKDOWN_BIN_PATH@|$(LOCKDOWN_BIN_PATH)|g'

.PHONY: dotfiles dev tools all

dotfiles:
	@echo "=== Dotfiles ==="
	# bashrc
	cp dotfiles/bashrc $(HOME)/.bashrc
	$(SUBST) $(HOME)/.bashrc
	# app config dirs
	for app in foot fuzzel sway waybar copyq ranger fzf zed; do \
		mkdir -p $(DEPLOY_DIR)/$$app; \
		cp -r dotfiles/$$app/* $(DEPLOY_DIR)/$$app/; \
	done
	# brave/firefox (policy dirs)
	cp -r dotfiles/brave/*   $(DEPLOY_DIR)/brave/
	cp -r dotfiles/firefox/* $(DEPLOY_DIR)/firefox/
	# copyq theme subdir
	mkdir -p $(DEPLOY_DIR)/copyq/themes
	cp -r dotfiles/copyq/themes/* $(DEPLOY_DIR)/copyq/themes/
	# waybar scripts subdir
	mkdir -p $(DEPLOY_DIR)/waybar/scripts
	cp -r dotfiles/waybar/scripts/* $(DEPLOY_DIR)/waybar/scripts/
	# obsidian (custom vault path)
	mkdir -p $(OBSIDIAN_VAULT_PATH)/.obsidian
	cp dotfiles/obsidian/* $(OBSIDIAN_VAULT_PATH)/.obsidian/
	@echo "Dotfiles deployed."

dev:
	@echo "=== Dev ==="
	mkdir -p $(DEPLOY_DIR)/opencode $(DEPLOY_DIR)/container
	cp dev/github.env       $(DEPLOY_DIR)/github.env
	chmod 600               $(DEPLOY_DIR)/github.env
	cp dev/opencode/*       $(DEPLOY_DIR)/opencode/
	@echo "Dev configs deployed."

tools:
	@echo "=== Tools ==="
	install -D -m 755 tools/check-firmware.sh $(LOCKDOWN_BIN_PATH)/check-firmware
	install -D -m 755 tools/help.sh $(LOCKDOWN_BIN_PATH)/help
	$(SUBST) $(LOCKDOWN_BIN_PATH)/check-firmware \
	         $(LOCKDOWN_BIN_PATH)/help

all: dotfiles dev
