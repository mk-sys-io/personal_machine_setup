#!/usr/bin/env bash
#
# init — bootstrap per-repo git workflow files.
#
# Scaffolds plans/ (lifecycle layout) + .ignore + .gitleaks.toml + biome.json.
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
# Biome policy: discovery, not injection — same rule as the gitleaks extender.
# The canonical config is dev/biome/shared.json, deployed by `make dev` to the
# config home, where Biome reads it as its LAST-resort fallback (CWD → parents
# → config home). This script writes a thin per-repo EXTENDER pinning
# `root: true`; globs and the `extends` target resolve from THIS repo. Never
# hand-edit a generated biome.json: edit dev/biome/shared.json and re-run
# `make dev`.
if [[ -d "$SCRIPT_DIR/../dev/biome" ]]; then
    CHECKOUT_BIOME="$(cd "$SCRIPT_DIR/../dev/biome" && pwd)/shared.json"
else
    CHECKOUT_BIOME="$SCRIPT_DIR/../dev/biome/shared.json"
fi
LIVE_BIOME="${XDG_CONFIG_HOME:-$HOME/.config}/biome/biome.json"

print_help() {
    cat <<'EOF'
Usage: init [--yes] [--help]

Bootstraps per-repo git workflow files.
Refuses to run directly in $HOME. Initialises a git repo when missing
(prompts, unless --yes).

Creates (only where missing):
  plans/<7 subdirs>/   AI plan files (git-ignored scratch)
  plans/README.md      lifecycle layout doc
  .ignore              re-include plans/ for ripgrep (@plans/ refs)
  .gitleaks.toml       secret-scan extender (points at machine-wide policy)
  biome.json           biome config extender (points at machine-wide policy)

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

# Hard dependency from the auto-git-init step below (previously pure scaffold).
command -v git >/dev/null 2>&1 || {
    echo "init: git not found — install git to scaffold a repo." >&2
    exit 1
}

# Never scaffold home itself: plans/ + .ignore + .gitleaks.toml would pollute $HOME.
if [[ "$(pwd -P)" == "$(cd "$HOME" && pwd -P)" ]]; then
    echo "init: refusing to run directly in \$HOME (cd into a project dir)." >&2
    exit 1
fi

# Auto git init (option A): a re-run inside an existing repo is a no-op by
# design — `git init` there only prints "Reinitialized ..." and keeps
# objects/refs/HEAD, but the `-b` flag could touch HEAD, so an existing repo
# skips the call entirely. A corrupt .git also fails rev-parse and is treated
# as "not a repo": offering `git init` there is the correct recovery.
git_result="repo present"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    do_init=0
    if [[ "$ASSUME_YES" -eq 1 ]]; then
        do_init=1
    else
        read -rp "Not a git repo. Run 'git init' here? [y/N] " reply || true
        if [[ "${reply:-}" =~ ^[Yy]$ ]]; then do_init=1; fi
    fi
    if [[ "$do_init" -eq 1 ]]; then
        git init || { echo "init: 'git init' failed." >&2; exit 1; }
        git_result="initialized"
    else
        git_result="skipped (not a repo — hook will not fire until 'git init')"
    fi
fi

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

if [[ -f biome.json || -f biome.jsonc ]]; then
    m_biome="="; n_biome="(exists, will skip)"; biome_new=0
elif [[ "$git_result" == skipped* ]]; then
    # vcs.useIgnoreFile needs a git work tree: without one biome hard-fails
    # ("couldn't find an ignore file"), so never write a config we cannot run.
    m_biome="-"; n_biome="(not a git repo — re-run after 'git init')"; biome_new=0
else
    m_biome="+"; n_biome="(policy extender)"; biome_new=1
fi

biome_src=""
for cand in "$CHECKOUT_BIOME" "$LIVE_BIOME"; do
    if [[ -s "$cand" ]]; then
        biome_src="$cand"
        break
    fi
done
if [[ "$biome_new" -eq 1 && -z "$biome_src" ]]; then
    m_biome="-"; n_biome="(no shared config — run 'make dev')"; biome_new=0
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
if [[ "$git_result" == "repo present" ]]; then
    printf '  %s %-18s %s\n' "=" "git/" "(repo present)"
elif [[ "$git_result" == "initialized" ]]; then
    printf '  %s %-18s %s\n' "+" "git/" "(initialized)"
else
    printf '  %s %-18s %s\n' "-" "git/" "(not a repo — hook will not fire)"
fi
printf '  %s %-18s %s\n' "$m_plans" "plans/" "$n_plans"
printf '  %s %-18s %s\n' "$m_readme" "plans/README.md" "$n_readme"
printf '  %s %-18s %s\n' "$m_ignore" ".ignore" "$n_ignore"
if [[ "$toml_new" -eq 1 ]]; then
    printf '  %s %-18s %s\n' "$m_toml" ".gitleaks.toml" "(secret-scan extender -> $template)"
else
    printf '  %s %-18s %s\n' "$m_toml" ".gitleaks.toml" "$n_toml"
fi
if [[ "$biome_new" -eq 1 ]]; then
    printf '  %s %-18s %s\n' "$m_biome" "biome.json" "(policy extender -> $biome_src)"
else
    printf '  %s %-18s %s\n' "$m_biome" "biome.json" "$n_biome"
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

if [[ "$biome_new" -eq 1 ]]; then
    # Thin extender, never a policy copy: one versioned policy, many repos.
    # `root: true` stops the ancestor walk at this repo so $HOME can never
    # become the biome project root; vcs.useIgnoreFile makes .gitignore
    # authoritative for both traversal and explicitly passed paths.
    cat > biome.json <<EOF
{
  "root": true,
  "vcs": { "enabled": true, "clientKind": "git", "useIgnoreFile": true },
  "extends": ["$biome_src"]
}
EOF
    if command -v biome >/dev/null 2>&1 && biome inspect config >/dev/null 2>&1; then
        biome_result="created (extends $biome_src, verified)"
    else
        biome_result="created (extends $biome_src)"
    fi
else
    biome_result="skipped"
fi
printf '  %s %-18s %s\n' "$m_biome" "biome.json" "$biome_result"

# Non-blocking: the global hook only lands when the binary exists
# (Makefile dev target). Warn when commits would scan under nothing.
hooks_path="$(git config --get core.hooksPath 2>/dev/null || true)"
if [[ -z "$hooks_path" ]]; then
    echo "warning: core.hooksPath is unset; the gitleaks pre-commit hook will not fire." >&2
elif [[ ! -x "$hooks_path/pre-commit" ]]; then
    echo "warning: hook '$hooks_path/pre-commit' is missing or not executable." >&2
fi

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
