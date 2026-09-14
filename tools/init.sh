#!/usr/bin/env bash
#
# init — bootstrap per-repo git workflow files.
#
# Scaffolds plans/ (lifecycle layout) + .ignore + .gitleaks.toml.
# Seed model: creates only what is missing, never modifies existing files.
# Run `init --help` for the layout doc.

set -euo pipefail

REPO_NAME=$(basename "$(pwd)")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_TOML="$SCRIPT_DIR/../dev/git/gitleaks.toml"
GLOBAL_IGNORE="${XDG_CONFIG_HOME:-$HOME/.config}/git/ignore"

print_help() {
    cat <<'EOF'
Usage: init [--yes] [--help]

Bootstraps per-repo git workflow files.

Creates (only where missing):
  plans/<7 subdirs>/   AI plan files (git-ignored scratch)
  plans/README.md      lifecycle layout doc
  .ignore              re-include plans/ for ripgrep (@plans/ refs)
  .gitleaks.toml       secret-scan config

Layout (plans/):
  active/     work in flight or up next (active/<topic>/*.md)
  completed/  finished work, kept for history
  reference/  specs and guides consulted but not "worked on"
  decisions/  ADRs and decision records
  postponed/  deferred; may be revisited
  archive/    deprecated or abandoned; archaeology only
  indexes/    navigation docs pointing at other plans

Rules:
  Seed model — existing files are never modified.
  To re-scaffold a file, delete it and re-run.
EOF
}

# Static gitleaks template (mirrors dev/git/gitleaks.toml). Embedded so the
# deployed single-file ~/.local/bin/init works on machines without this
# checkout. If dev/git/gitleaks.toml gains new content, update this copy too.
gitleaks_template() {
    cat <<'EOF'
title = "gitleaks config"

[extend]
useDefault = true

[[allowlists]]
description = "Allow template files with placeholder values"
paths = [
    '''\.template$''',
    '''\.example$''',
]
EOF
}

plans_readme() {
    cat <<'EOF'
# plans/ — lifecycle layout (git-ignored scratch, machine-local)

- `active/` — work in flight or up next (`active/<topic>/*.md`).
- `completed/` — finished work, kept for history.
- `reference/` — specs and guides consulted but not "worked on".
- `decisions/` — ADRs and decision records.
- `postponed/` — deferred; may be revisited.
- `archive/` — deprecated or abandoned; archaeology only.
- `indexes/` — navigation docs pointing at other plans.

Transitions: `active/` → `completed/` on ship;
`active/` → `postponed/` on defer; `postponed/` → `active/`
on revisit, or → `archive/` on drop. Never commit `plans/`.
EOF
}

ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y) ASSUME_YES=1 ;;
        --help|-h) print_help; exit 0 ;;
        *) echo "init: unknown argument: $arg (see init --help)" >&2; exit 2 ;;
    esac
done

# Preview: compute per-file status before confirming.
# Marks: "+" will change, "=" already present.
plans_complete=1
for d in active completed reference decisions postponed archive indexes; do
    if [[ ! -d plans/$d ]]; then
        plans_complete=0
        break
    fi
done
if [[ "$plans_complete" -eq 1 ]]; then
    m_plans="="; n_plans="(subdirs present)"
else
    m_plans="+"; n_plans="(7 subdirs)"
fi

if [[ -f plans/README.md ]]; then
    m_readme="="; n_readme="(exists, will skip)"; readme_new=0
else
    m_readme="+"; n_readme="(lifecycle layout doc)"; readme_new=1
fi

if [[ ! -f .ignore ]]; then
    m_ignore="+"; n_ignore="(re-include for @plans/ refs)"; ignore_state="missing"
elif grep -qxF '!plans/' .ignore 2>/dev/null; then
    m_ignore="="; n_ignore="(already has '!plans/')"; ignore_state="present"
else
    m_ignore="+"; n_ignore="(will append '!plans/')"; ignore_state="append"
fi

if [[ -f .gitleaks.toml ]]; then
    m_toml="="; n_toml="(exists, will skip)"; toml_new=0
else
    m_toml="+"; n_toml="(secret-scan config)"; toml_new=1
fi

echo "Bootstrapping '$REPO_NAME':"
printf '  %s %-18s %s\n' "$m_plans" "plans/" "$n_plans"
printf '  %s %-18s %s\n' "$m_readme" "plans/README.md" "$n_readme"
printf '  %s %-18s %s\n' "$m_ignore" ".ignore" "$n_ignore"
printf '  %s %-18s %s\n' "$m_toml" ".gitleaks.toml" "$n_toml"
echo "Existing files are left untouched."
if [[ "$ASSUME_YES" -ne 1 ]]; then
    read -rp "Proceed? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || exit 0
fi
echo ""

mkdir -p plans/active plans/completed plans/reference plans/decisions \
    plans/postponed plans/archive plans/indexes
printf '  %s %-18s %s\n' "$m_plans" "plans/" "ensured"

if [[ "$readme_new" -eq 1 ]]; then
    plans_readme > plans/README.md
    readme_result="created"
else
    readme_result="skipped (exists)"
fi
printf '  %s %-18s %s\n' "$m_readme" "plans/README.md" "$readme_result"

case "$ignore_state" in
    missing)
        echo '!plans/' > .ignore
        ignore_result="created"
        ;;
    append)
        echo '!plans/' >> .ignore
        ignore_result="appended '!plans/'"
        ;;
    present)
        ignore_result="unchanged"
        ;;
esac
printf '  %s %-18s %s\n' "$m_ignore" ".ignore" "$ignore_result"

if [[ "$toml_new" -eq 1 ]]; then
    if [[ -f "$TEMPLATE_TOML" ]]; then
        cp "$TEMPLATE_TOML" .gitleaks.toml
    else
        gitleaks_template > .gitleaks.toml
    fi
    toml_result="created"
else
    toml_result="skipped (exists)"
fi
printf '  %s %-18s %s\n' "$m_toml" ".gitleaks.toml" "$toml_result"

if ! grep -q 'plans/' "$GLOBAL_IGNORE" 2>/dev/null; then
    {
        echo ""
        echo "warning: global gitignore '$GLOBAL_IGNORE' does not exclude plans/;"
        echo "warning: run lib/50-github_setup.sh or add 'plans/' to avoid committing plans/"
    } >&2
fi

echo "done."
