#!/usr/bin/env bash
set -euo pipefail

# init — Bootstrap plans/ folder + .ignore for opencode @ references
# Run in any repo root to start drafting AI agent plans locally.

mkdir -p plans
echo '!plans/' > .ignore
echo "done: plans/ + .ignore"
