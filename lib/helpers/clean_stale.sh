#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# clean_stale.sh — Orphan sweeper for Makefile-managed deploy dirs (30-B)
#
# Usage: bash lib/helpers/clean_stale.sh (or: make clean-stale)
#   YES=1 skips the prompt (scripts/CI, incl. no-TTY runs).
#   DEPLOY_DIR (default $HOME/.config), BIN_DIR (default $HOME/.local/bin).
#
# Oracle: `git ls-files -z -- <src>` piped through tr/sed to strip the
# `<src>/` prefix = expected top-level relpaths (no sort contract,
# locale-independent); `find <dest>` (files + symlinks, `.*` skipped) =
# actual; `grep -Fxv` diff, minus is_excluded names + per-pair extras.
#
# Manifest (explicit, repo-is-truth; per-app subdir scope only):
#   sweep_exclusive <src-rel> <dest-abs> [extra...] — unknown dest files
#   are orphans (deleted on confirm). sweep_additive — never deletes,
#   registers dest for empty-dir prune only; default for new apps, promote
#   to exclusive only for 1:1-owned dirs. Non-1:1 (obsidian vault, xdg
#   flat file, flat files, symlinks, generated paths) stays unlisted.
#   BIN_DIR history-derived: repo-deleted top-level tools/* only
#   (extension stripped, subdir deletions ignored).
#
# Safety: assert_scope refuses $HOME top-level + Downloads/Documents/
#   Music/Pictures/Videos (fail-closed); files + symlinks only (dirs are
#   rmdir'd only when empty, excluded dirs never pruned); empty-manifest
#   src skips (never nukes); missing src silently skips (dest kept — app
#   removal needs a one-time manual dest rm); missing dest silently skips
#   (optionally-undeployed).
#
# Prompt (single gate, no backup, no preview mode): lists orphans;
#   total>20 prints a WARNING (advisory, same prompt); strict `y` deletes
#   and exits 0 (deploy continues in the same `make dotfiles` run);
#   anything else exits 1 (aborts `dotfiles: clean-stale`). No-TTY aborts
#   unless YES=1.
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/.config}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
YES="${YES:-}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ORPHANS="$TMP/orphans"
: > "$ORPHANS"
: > "$TMP/dests"

die() { echo "clean_stale.sh: $*" >&2; exit 1; }

[ -n "$DEPLOY_DIR" ] || die "refusing empty DEPLOY_DIR"
[ "$DEPLOY_DIR" != "/" ] || die "refusing DEPLOY_DIR=/"

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

# sweep_exclusive <src-rel> <dest-abs> [extra-exclude-top-relpath...]
# Files+symlinks only (dirs are never rm'd, only guard-pruned when empty).
# Dest dotfiles skipped (deploy loop never copies `.*` names).
sweep_exclusive() {
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

# sweep_additive <src-rel> <dest-abs>
# Never deletes: registers dest for empty-dir prune only. Default for new
# apps; promote to exclusive only for 1:1-owned dirs.
sweep_additive() {
    local src_rel="$1" dest="$2"
    assert_scope "$dest"
    [ -d "$REPO_ROOT/$src_rel" ] || return 0
    [ -d "$dest" ] || return 0
    echo "$dest" >> "$TMP/dests"
}

# --- Explicit sweep manifest (repo-is-truth; per-app subdir scope only). ---
# Unknown = dest file not in `git ls-files <src>` for an exclusive pair.
# Additive pairs never delete (empty-dir prune only); default for new apps.
# Non-1:1 deploys (obsidian vault path, xdg flat file, flat files,
# symlinks) stay unlisted. Removing an app dir from the repo needs a
# one-time manual dest rm (§30.5 rule).
sweep_exclusive dotfiles/browsers "$DEPLOY_DIR/browsers"
sweep_exclusive dotfiles/clipse "$DEPLOY_DIR/clipse"
sweep_exclusive dotfiles/environment.d "$DEPLOY_DIR/environment.d"
sweep_exclusive dotfiles/espanso "$DEPLOY_DIR/espanso"
sweep_exclusive dotfiles/fastfetch "$DEPLOY_DIR/fastfetch"
sweep_exclusive dotfiles/fzf "$DEPLOY_DIR/fzf"
sweep_exclusive dotfiles/gtk-3.0 "$DEPLOY_DIR/gtk-3.0"
sweep_exclusive dotfiles/gtk-4.0 "$DEPLOY_DIR/gtk-4.0"
sweep_exclusive dotfiles/kitty "$DEPLOY_DIR/kitty"
sweep_exclusive dotfiles/mpv "$DEPLOY_DIR/mpv"
sweep_exclusive dotfiles/sway "$DEPLOY_DIR/sway"
sweep_exclusive dotfiles/systemd "$DEPLOY_DIR/systemd"
sweep_exclusive dotfiles/waybar "$DEPLOY_DIR/waybar"
sweep_exclusive dotfiles/wayland-pipewire-idle-inhibit "$DEPLOY_DIR/wayland-pipewire-idle-inhibit"
sweep_exclusive dotfiles/yazi "$DEPLOY_DIR/yazi"
# opencode extras mirror the dev-target deploy excludes: a live docs/ (etc.)
# is user-created until proven otherwise — never delete by oracle alone.
sweep_exclusive dev/opencode "$DEPLOY_DIR/opencode" docs README.md tsconfig.json types
sweep_exclusive dev/pi/extensions "$HOME/.pi/agent/extensions"

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

# --- Single prompt gate: list, ask once, delete or abort in this run. ---
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
if [ "$total" -gt 20 ]; then
    echo "clean_stale.sh: WARNING — $total orphans (> 20), review carefully."
fi
if [ -n "${YES:-}" ]; then
    confirm="y"
else
    read -rp "clean_stale.sh: delete $total orphans? [y/N] " confirm < /dev/tty || exit 1
fi
case "$confirm" in
    [Yy]) ;;
    *) echo "clean_stale.sh: declined — deploy aborted."; exit 1 ;;
esac
while IFS=$'\t' read -r dest rel; do
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
