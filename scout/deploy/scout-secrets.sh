#!/usr/bin/env bash
# scout/deploy/scout-secrets.sh — generate the machine-local scout secrets on first deploy.
# Repo truth: the script is committed; the secret *values* never are.
# Contract:
#   - create-if-absent, never overwrite (regenerable on a fresh machine; untouched on existing)
#   - umask 077 + mktemp in target dir + atomic mv; mode 600 always
#   - fail loud: salt placeholder present, OR missing pg.env with an existing Postgres volume
#   - no secret values on stdout/stderr/journal
# No sudo, no net. Invoked as a `secrets` prerequisite of `scout/Makefile`'s `scout` target.
set -euo pipefail

SCOUT_DIR="${SCOUT_SECRETS_DIR:-$HOME/.config/scout}"
PG_ENV="$SCOUT_DIR/pg.env"
SCOUT_ENV="$SCOUT_DIR/scout.env"
VOLUME_NAME="scout-pgdb"
SALT_PLACEHOLDER="LITELLM_SALT_KEY=__SALT_PLACEHOLDER_REPLACE_ME__"

err() { printf 'scout-secrets: ERROR %s\n' "$*" >&2; }

# Ensure the directory exists with tight perms.
mkdir -p "$SCOUT_DIR"
chmod 700 "$SCOUT_DIR"

# Salt guard: refuse before any unit installs if the operator hasn't pasted the salt yet.
if [[ -f "$SCOUT_ENV" ]] && grep -qF "$SALT_PLACEHOLDER" "$SCOUT_ENV"; then
    err "LITELLM_SALT_KEY is still the placeholder in $SCOUT_ENV."
    err "Paste the real salt from your password manager into LITELLM_SALT_KEY=, then re-run 'make scout'."
    exit 1
fi

# Volume guard: missing pg.env alongside an existing Postgres volume = do not regenerate.
if [[ ! -f "$PG_ENV" ]] && podman volume exists "$VOLUME_NAME" 2>/dev/null; then
    err "pg.env is missing but the Postgres volume '$VOLUME_NAME' already exists."
    err "Restore $PG_ENV from backup; do NOT regenerate (password would no longer match the volume)."
    exit 1
fi

# Idempotent: if both files exist, just re-assert mode and exit.
if [[ -f "$PG_ENV" && -f "$SCOUT_ENV" ]]; then
    chmod 600 "$PG_ENV" "$SCOUT_ENV"
    printf 'scout-secrets: existing secrets present; mode re-asserted (600).\n'
    exit 0
fi

# Create-if-absent. Atomic write via mktemp in the same directory (same filesystem).
tmp_pg="$(mktemp "$SCOUT_DIR/.pg.env.XXXXXX")"
tmp_scout="$(mktemp "$SCOUT_DIR/.scout.env.XXXXXX")"
cleanup() { rm -f "$tmp_pg" "$tmp_scout"; }
trap cleanup EXIT

PG_PASSWORD="$(openssl rand -hex 32)"
MASTER_KEY="sk-$(openssl rand -hex 32)"

{
    printf 'POSTGRES_USER=litellm\n'
    printf 'POSTGRES_DB=litellm\n'
    printf 'POSTGRES_PASSWORD=%s\n' "$PG_PASSWORD"
} >"$tmp_pg"

{
    printf 'DATABASE_URL=postgresql://litellm:%s@127.0.0.1:5432/litellm\n' "$PG_PASSWORD"
    printf 'LITELLM_MASTER_KEY=%s\n' "$MASTER_KEY"
    printf '%s\n' "$SALT_PLACEHOLDER"
    printf '\n# Operator: replace the placeholder above with the real LITELLM_SALT_KEY from\n'
    printf '# your password manager. Never rotate it after models are added.\n'
} >"$tmp_scout"

chmod 600 "$tmp_pg" "$tmp_scout"
mv -f "$tmp_pg" "$PG_ENV"
mv -f "$tmp_scout" "$SCOUT_ENV"
trap - EXIT

printf 'scout-secrets: generated %s and %s (mode 600).\n' "$PG_ENV" "$SCOUT_ENV"
printf 'scout-secrets: action required — paste LITELLM_SALT_KEY into %s before first run.\n' "$SCOUT_ENV"