.PHONY: config

DIRS := sway waybar foot fuzzel copyq/themes waybar/scripts zed opencode ranger

config:
	@for d in $(DIRS); do mkdir -p $(HOME)/.config/$$d; done
	cp .config/sway/sway_config          $(HOME)/.config/sway/config
	cp .config/waybar/waybar_config.json $(HOME)/.config/waybar/config.json
	cp .config/waybar/style.css          $(HOME)/.config/waybar/style.css
	cp .config/waybar/mocha.css          $(HOME)/.config/waybar/mocha.css
	cp .config/foot/foot.ini             $(HOME)/.config/foot/foot.ini
	cp .config/fuzzel/fuzzel.ini         $(HOME)/.config/fuzzel/fuzzel.ini
	cp .config/copyq/copyq.conf          $(HOME)/.config/copyq/copyq.conf
	cp .config/copyq/themes/*            $(HOME)/.config/copyq/themes/
	cp .config/waybar/scripts/*          $(HOME)/.config/waybar/scripts/
	cp .config/zed/settings.json         $(HOME)/.config/zed/settings.json
	cp .config/opencode/opencode.jsonc   $(HOME)/.config/opencode/opencode.jsonc
	cp .config/ranger/rc.conf            $(HOME)/.config/ranger/rc.conf
	cp .config/obsidian/appearance.json  $(HOME)/knowledge_base/.obsidian/appearance.json
	@echo "User configs reloaded"
