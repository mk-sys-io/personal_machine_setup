# scout/Makefile — deploy step for the scout component (D17).
# Shelled out from the root Makefile via `$(MAKE) -C scout` so the root
# CONTRACT header stays intact. No sudo, no net, no clean-stale.
# Sources: providers/, blacklist.yaml, scout.py, units/*.
# Live targets: ~/.config/scout/, ~/.local/bin/scout, ~/.config/systemd/user/.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DELETE_ON_ERROR:

SCOUT_HOME := $(HOME)/.config/scout
SCOUT_BIN := $(HOME)/.local/bin/scout
STATE_DIR := $(HOME)/.local/state/scout
SYSTEMD_USER := $(HOME)/.config/systemd/user

UNITS := scout.service scout.timer scout-reloader.path

.PHONY: scout sync units

scout: sync units
	@echo "=== Scout install ==="
	mkdir -p $(STATE_DIR)
	systemctl --user daemon-reload
	@echo "Scout units installed + daemon-reload complete (enable deferred to Phase 6)."

sync:
	@echo "=== Scout sync ==="
	mkdir -p $(SCOUT_HOME)/providers
	# one-way sync per tree; --delete so provider removal propagates
	rsync -a --delete scout/providers/ $(SCOUT_HOME)/providers/
	# binary (tools: pattern — strip extension)
	install -m 755 scout/scout.py $(SCOUT_BIN)
	# blacklist.yaml is optional until Phase 7; tolerate absence
	if [ -f scout/blacklist.yaml ]; then \
		cp scout/blacklist.yaml $(SCOUT_HOME)/blacklist.yaml; \
	fi
	# docs — copy if present (Phase 8 artifact)
	if [ -d scout/docs ]; then \
		mkdir -p $(SCOUT_HOME)/docs; \
		rsync -a --delete scout/docs/ $(SCOUT_HOME)/docs/; \
	fi
	@echo "Scout synced."

units:
	@echo "=== Scout units ==="
	mkdir -p $(SYSTEMD_USER)
	for u in $(UNITS); do \
		cp scout/units/$$u $(SYSTEMD_USER)/$$u; \
	done
	@echo "Scout units installed (not enabled)."
