#!/usr/bin/env bash
set -euo pipefail

REPO_NAME=$(basename "$(pwd)")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Bootstrapping git workflow for: $REPO_NAME"
echo ""
echo "This will create:"
echo "  plans/            - opencode plan files (git-excluded)"
echo "  .ignore           - ripgrep re-include for @plans/ refs"
echo "  .gitleaks.toml    - secret scanning config (allowlists)"
echo ""
read -rp "Proceed? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || exit 0

mkdir -p plans
echo '!plans/' > .ignore

cp "$SCRIPT_DIR/../dev/git/gitleaks.toml" .gitleaks.toml
sed -i "s/REPO_NAME/$REPO_NAME/" .gitleaks.toml

echo "done: plans/ + .ignore + .gitleaks.toml"
