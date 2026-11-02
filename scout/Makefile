# scout/Makefile — deploy step for the scout component. No sudo in any target:
# if a step ever needs privileges, the whole chain moves into lib/ + install.py,
# never into a Makefile recipe. Shelled out from the root Makefile via
# `$(MAKE) -C scout` so the root CONTRACT header stays intact.
# Sources: providers/, blacklist.yaml, scout.py, units/*, deploy/*.
# Live targets: ~/.config/scout/, ~/.local/bin/scout, ~/.config/systemd/user/,
#   ~/.config/containers/systemd/, ~/.local/state/scout/.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DELETE_ON_ERROR:

SCOUT_HOME := $(HOME)/.config/scout
SCOUT_BIN := $(HOME)/.local/bin/scout
STATE_DIR := $(HOME)/.local/state/scout
SYSTEMD_USER := $(HOME)/.config/systemd/user
CONTAINERS_SYSTEMD := $(HOME)/.config/containers/systemd

UNITS := scout.service scout.timer scout-reloader.path scout-litellm.service

.PHONY: scout secrets sync units

scout: secrets sync units
	@echo "=== Scout install ==="
	mkdir -p $(STATE_DIR)
	systemctl --user daemon-reload
	systemctl --user enable scout.service scout.timer scout-reloader.path scout-pgdb.service scout-litellm.service
	@echo "Scout units installed + enabled + daemon-reload complete (starts at acceptance)."

secrets:
	@echo "=== Scout secrets ==="
	bash deploy/scout-secrets.sh

sync:
	@echo "=== Scout sync ==="
	mkdir -p $(SCOUT_HOME)/providers
	# one-way sync per tree; --delete so provider removal propagates
	rsync -a --delete providers/ $(SCOUT_HOME)/providers/
	# binary (tools: pattern — strip extension)
	install -m 755 scout.py $(SCOUT_BIN)
	# blacklist.yaml is optional until seeded; tolerate absence
	if [ -f blacklist.yaml ]; then \
		cp blacklist.yaml $(SCOUT_HOME)/blacklist.yaml; \
	fi
	# docs — copy if present
	if [ -d docs ]; then \
		mkdir -p $(SCOUT_HOME)/docs; \
		rsync -a --delete docs/ $(SCOUT_HOME)/docs/; \
	fi
	# litellm substrate artifacts
	mkdir -p $(SCOUT_HOME)
	cp deploy/config.yaml $(SCOUT_HOME)/config.yaml
	chmod 600 $(SCOUT_HOME)/config.yaml
	@echo "Scout synced."

units:
	@echo "=== Scout units ==="
	mkdir -p $(SYSTEMD_USER)
	for u in $(UNITS); do \
		cp units/$$u $(SYSTEMD_USER)/$$u; \
	done
	# quadlet files live in the containers systemd dir (not the user unit dir)
	mkdir -p $(CONTAINERS_SYSTEMD)
	cp deploy/scout-pgdb.container $(CONTAINERS_SYSTEMD)/scout-pgdb.container
	cp deploy/scout-pgdb.volume $(CONTAINERS_SYSTEMD)/scout-pgdb.volume
	@echo "Scout units installed (not enabled beyond 'scout' enable step)."