#!/usr/bin/env bash
# Run alembic migrations against Supabase production DB.
# Reads DATABASE_URL_SUPABASE (and SUPABASE_DB_PASSWORD it depends on) from .env.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found in $ROOT_DIR" >&2
  exit 1
fi

# Export .env vars so ${SUPABASE_DB_PASSWORD} interpolation works.
set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ -z "${SUPABASE_DB_PASSWORD:-}" ]]; then
  echo "ERROR: SUPABASE_DB_PASSWORD is empty in .env" >&2
  echo "Get it from Supabase Dashboard -> Settings -> Database -> Connection String" >&2
  exit 1
fi

if [[ -z "${DATABASE_URL_SUPABASE:-}" ]]; then
  echo "ERROR: DATABASE_URL_SUPABASE is empty in .env" >&2
  exit 1
fi

# Re-expand ${SUPABASE_DB_PASSWORD} inside DATABASE_URL_SUPABASE
RESOLVED_URL=$(eval echo "$DATABASE_URL_SUPABASE")

echo ">>> Running alembic upgrade head against Supabase..."
ALEMBIC_DATABASE_URL="$RESOLVED_URL" alembic upgrade head

echo ">>> Current alembic revision on Supabase:"
ALEMBIC_DATABASE_URL="$RESOLVED_URL" alembic current
