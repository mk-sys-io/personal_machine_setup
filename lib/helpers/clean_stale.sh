#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# clean_stale.sh — Orphan sweeper for Makefile-managed deploy dirs (30-B)
#
# Usage: bash lib/helpers/clean_stale.sh
#   Env gates: CONFIRM=1 deletes (unconfirmed run prints + exits 1 —
#   preview and apply share this one code path). FORCE=1 required past
#   20 deletions. BACKUP_DIR set => copy-before-delete there.
#   DEPLOY_DIR (default $HOME/.config), BIN_DIR (default $HOME/.local/bin).
#
# Design: manifest oracle (`git ls-files`) matched with `grep -Fxvz`
# (no sort contract, locale-independent). Sweep set derived at runtime
# from dotfiles/*/ minus DOT_SWEEP_DENY; dev/ pairs explicit.
# ~/.local/bin swept history-derived (repo-deleted tools only).
# Never touches $HOME top-level or the 7 skeleton dirs (fail-closed).
#
# Maintenance: adding dotfiles/<app>/ needs zero changes. Removing an
# app dir from the repo needs a one-time manual dest rm (§30.5 rule).
# A new non-1:1 deploy (next obsidian/xdg) must extend DOT_SWEEP_DENY.
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/.config}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CONFIRM="${CONFIRM:-}"
FORCE="${FORCE:-}"
BACKUP_DIR="${BACKUP_DIR:-}"
# Non-1:1 deploys under dotfiles/ (obsidian→vault path, xdg→flat file).
DOT_SWEEP_DENY="${DOT_SWEEP_DENY:-obsidian xdg}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ORPHANS="$TMP/orphans"
: > "$ORPHANS"
: > "$TMP/dests"

die() { echo "clean_stale.sh: $*" >&2; exit 1; }

[ -n "$DEPLOY_DIR" ] || die "refusing empty DEPLOY_DIR"
[ "$DEPLOY_DIR" != "/" ] || die "refusing DEPLOY_DIR=/"
case "${BACKUP_DIR:-}" in
    "$REPO_ROOT"|"$REPO_ROOT"/*) die "BACKUP_DIR must not live in the repo tree" ;;
esac

# Skeleton assert: no swept dest may be $HOME or under a skeleton dir.
assert_scope() {
    case "$1" in
        "$HOME"|"$HOME"/Downloads|"$HOME"/Downloads/*|"$HOME"/Documents|"$HOME"/Documents/*|"$HOME"/Music|"$HOME"/Music/*|"$HOME"/Pictures|"$HOME"/Pictures/*|"$HOME"/Videos|"$HOME"/Videos/*)
            die "REFUSING scope violation: $1" ;;
    esac
}

# Runtime-owned names: never delete, never prune.
# (providers.json = pi-setup-generated provider list, same class as curated/.)
is_excluded() {
    local rel="$1" base
    base="$(basename "$rel")"
    case "$rel" in
        *.wants*|*match/packages*|*node_modules*|*curated*) return 0 ;;
    esac
    case "$base" in
        *.service|*.timer|*.socket|*.path|*.state|.gitignore|package.json|package-lock.json|clipboard_history.json|clipse.log|tmp_files|auth.json|settings.json|auth.json.bak|providers.json) return 0 ;;
    esac
    return 1
}

# sweep_pair <src-rel> <dest-abs> [extra-exclude-top-relpath...]
# Files+symlinks only (dirs are never rm'd, only guard-pruned when empty).
# Dest dotfiles skipped (deploy loop never copies `.*` names).
sweep_pair() {
    local src_rel="$1" dest="$2" rel extra skip
    shift 2
    assert_scope "$dest"
    [ -d "$REPO_ROOT/$src_rel" ] || return 0
    [ -d "$dest" ] || return 0
    echo "$dest" >> "$TMP/dests"
    (cd "$REPO_ROOT" && git ls-files -z -- "$src_rel") | tr '\0' '\n' | sed "s#^${src_rel}/##" > "$TMP/expected"
    # Empty manifest (all-ignored src) => cannot oracle; skip, don't nuke.
    [ -s "$TMP/expected" ] || return 0
    find "$dest" -mindepth 1 \( -type f -o -type l \) -not -name '.*' -printf '%P\n' > "$TMP/actual"
    grep -Fxv -f "$TMP/expected" "$TMP/actual" > "$TMP/raw" || true
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        is_excluded "$rel" && continue
        skip=0
        for extra in "$@"; do
            [ "$rel" = "$extra" ] && skip=1
        done
        [ "$skip" -eq 1 ] && continue
        printf '%s\t%s\n' "$dest" "$rel" >> "$ORPHANS"
    done < "$TMP/raw"
}

# --- Derived dotfiles sweep: every dotfiles/ dir 1:1, minus denylist. ---
for src in "$REPO_ROOT"/dotfiles/*/; do
    name="$(basename "$src")"
    case " $DOT_SWEEP_DENY " in
        *" $name "*) continue ;;
    esac
    sweep_pair "dotfiles/$name" "$DEPLOY_DIR/$name"
done

# --- Explicit dev pairs (heterogeneous deploys; derivation would lie). ---
# opencode extras mirror the dev-target deploy excludes: a live docs/ (etc.)
# is user-created until proven otherwise — never delete by oracle alone.
sweep_pair dev/opencode "$DEPLOY_DIR/opencode" docs README.md tsconfig.json types
sweep_pair dev/pi/extensions "$HOME/.pi/agent/extensions"

# --- ~/.local/bin: history-derived (repo-deleted tools only). ---
# Current-owned names first; deleted-history minus owned = sweep set.
# Top-level tools/ files only (deploy loop skips dirs); subdir deletions
# (provider_registry/*.py) never produced binaries and are filtered out.
if [ -d "$BIN_DIR" ]; then
    assert_scope "$BIN_DIR"
    : > "$TMP/owned"
    for script in "$REPO_ROOT"/tools/*; do
        [ -f "$script" ] || continue
        name="$(basename "$script")"
        printf '%s\n' "${name%.*}" >> "$TMP/owned"
    done
    [ -d "$REPO_ROOT/tools/pi_setup" ] && printf 'pi-setup\n' >> "$TMP/owned"
    (cd "$REPO_ROOT" && git log --diff-filter=D --name-only --format= -- tools/) \
        | sed -n 's#^tools/\([^/]*\)$#\1#p' | sed 's/\.[^.]*$//' | sort -u > "$TMP/deleted"
    grep -Fxv -f "$TMP/owned" "$TMP/deleted" > "$TMP/binraw" || true
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        { [ -e "$BIN_DIR/$name" ] || [ -L "$BIN_DIR/$name" ]; } || continue
        printf '%s\t%s\n' "$BIN_DIR" "$name" >> "$ORPHANS"
    done < "$TMP/binraw"
fi

# --- Gates: preview and apply share this one code path. ---
total="$(wc -l < "$ORPHANS")"
if [ "$total" -eq 0 ]; then
    echo "clean_stale.sh: no orphans."
    exit 0
fi
# Tab-split below is the sanctioned $'\t' exception (plan §30.1 item 3):
# the stream is writer-controlled (printf %s\t%s), never user input.
while IFS=$'\t' read -r dest rel; do
    echo "  $dest/$rel"
done < "$ORPHANS"
if [ -z "$CONFIRM" ]; then
    echo "clean_stale.sh: $total orphans listed above — re-run with CONFIRM=1 to delete."
    exit 1
fi
if [ "$total" -gt 20 ] && [ "$FORCE" != "1" ]; then
    echo "clean_stale.sh: $total orphans > 20 — re-run with FORCE=1."
    exit 1
fi
if [ -n "$BACKUP_DIR" ]; then
    mkdir -p "$BACKUP_DIR"
fi
while IFS=$'\t' read -r dest rel; do
    if [ -n "$BACKUP_DIR" ]; then
        (cd "$dest" && cp -a --parents "$rel" "$BACKUP_DIR/")
    fi
    echo "  removing $dest/$rel"
    rm -rf -- "${dest:?empty dest}/${rel:?empty rel}"
done < "$ORPHANS"
# Orphan empty-dir prune (guarded: excluded dirs are never pruned).
sort -u "$TMP/dests" | while IFS= read -r d; do
    [ -d "$d" ] || continue
    find "$d" -mindepth 1 -type d -empty -print0 | while IFS= read -r -d '' dd; do
        rel="${dd#"$d"/}"
        is_excluded "$rel" && continue
        rmdir "$dd" 2>/dev/null || true
    done
done
echo "clean_stale.sh: done."
