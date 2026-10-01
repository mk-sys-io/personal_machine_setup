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
# Canonical absolute paths: gitleaks resolves [extend] path from its own cwd,
# so it must not carry a ".." segment into a generated file. The checkout
# sibling does not exist on a live machine (~/.local/bin/../dev/git), so the
# cd is conditional — under `set -e` an unconditional one would abort with a
# raw cd error instead of falling through to the live path.
if [[ -d "$SCRIPT_DIR/../dev/git" ]]; then
    CHECKOUT_TOML="$(cd "$SCRIPT_DIR/../dev/git" && pwd)/gitleaks.toml"
else
    CHECKOUT_TOML="$SCRIPT_DIR/../dev/git/gitleaks.toml"
fi
LIVE_TOML="${XDG_CONFIG_HOME:-$HOME/.config}/init/gitleaks.toml"
GLOBAL_IGNORE="${XDG_CONFIG_HOME:-$HOME/.config}/git/ignore"

print_help() {
    cat <<'EOF'
Usage: init [--yes] [--help]

Bootstraps per-repo git workflow files.

Creates (only where missing):
  plans/<7 subdirs>/   AI plan files (git-ignored scratch)
  plans/README.md      lifecycle layout doc
  .ignore              re-include plans/ for ripgrep (@plans/ refs)
  .gitleaks.toml       secret-scan extender (points at machine-wide policy)

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

# gitleaks policy: discovery, not injection. The canonical policy is
# dev/git/gitleaks.toml, deployed by `make dev` to the live init path
# (~/.config/init/gitleaks.toml). This script reads whichever copy exists —
# checkout first (dev machine), then live (any other machine) — and writes a
# thin per-repo EXTENDER that points at it. Never hand-edit a generated
# .gitleaks.toml: edit dev/git/gitleaks.toml and re-run `make dev`.
#
# Merge semantics (gitleaks, verified on upstream 8.30.1): allowlist arrays
# APPEND across the chain (duplicates permitted), depth <=2, and `useDefault`
# and `path` are mutually exclusive. Extenders are add-only by convention,
# NOT enforced by the tool: a `[[rules]]` block re-declaring an inherited
# rule's `id` overrides it, and `disabledRules` suppresses named rules. So:
# never reuse an inherited id, never set disabledRules in an extender.

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

# Refuse BEFORE the preview: never name a .gitleaks.toml we cannot create.
# A missing template is a broken deploy (make dev never ran), not a user error.
template=""
for cand in "$CHECKOUT_TOML" "$LIVE_TOML"; do
    if [[ -s "$cand" ]]; then
        template="$cand"
        break
    fi
done
if [[ -z "$template" ]]; then
    {
        echo "init: no gitleaks policy found — checked, in order:"
        echo "  $CHECKOUT_TOML"
        echo "  $LIVE_TOML"
        echo "init: run 'make dev' in the linux_setup checkout to deploy one."
    } >&2
    exit 1
fi

echo "Bootstrapping '$REPO_NAME':"
printf '  %s %-18s %s\n' "$m_plans" "plans/" "$n_plans"
printf '  %s %-18s %s\n' "$m_readme" "plans/README.md" "$n_readme"
printf '  %s %-18s %s\n' "$m_ignore" ".ignore" "$n_ignore"
if [[ "$toml_new" -eq 1 ]]; then
    printf '  %s %-18s %s\n' "$m_toml" ".gitleaks.toml" "(secret-scan extender -> $template)"
else
    printf '  %s %-18s %s\n' "$m_toml" ".gitleaks.toml" "$n_toml"
fi
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
    # Thin extender, never a policy copy: one versioned policy, many repos.
    # $template is an absolute path (discovery above), so this resolves the
    # same whether gitleaks runs from the repo root or anywhere else.
    cat > .gitleaks.toml <<EOF
# Per-repo gitleaks config — scaffolded by init ($REPO_NAME).
#
# Policy lives in dev/git/gitleaks.toml (this checkout) or its deployed copy
# (~/.config/init/gitleaks.toml on other machines); this is a thin extender
# that points at whichever copy init found. Add repo-local suppressions as
# [[allowlists]] blocks below (they append to the inherited list). Do NOT
# re-declare an inherited rule id and do NOT set disabledRules here — either
# silently neutralises the shared policy.
title = "$REPO_NAME gitleaks config"

[extend]
path = "$template"
EOF
    toml_result="created (extends $template)"
else
    toml_result="skipped (exists)"
fi
printf '  %s %-18s %s\n' "$m_toml" ".gitleaks.toml" "$toml_result"

if [[ ! -f "$GLOBAL_IGNORE" ]]; then
    {
        echo "warning: global gitignore '$GLOBAL_IGNORE' is missing;"
        echo "warning: run 'make dev' in the linux_setup checkout to deploy it,"
        echo "warning: otherwise plans/ is not excluded and may be committed."
    } >&2
elif ! grep -q 'plans/' "$GLOBAL_IGNORE" 2>/dev/null; then
    {
        echo "warning: global gitignore '$GLOBAL_IGNORE' does not exclude plans/;"
        echo "warning: add 'plans/' to it to avoid committing plans/."
    } >&2
fi

echo "done."
