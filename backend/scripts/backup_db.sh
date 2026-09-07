#!/usr/bin/env bash
# Off-Supabase logical backup of the Muddato Postgres database (#71).
#
# Runs pg_dump (custom/compressed format) for Muddato's application-owned public
# schema and uploads it to S3-compatible object storage, so a recovery copy lives
# OUTSIDE Supabase (defense against a Supabase account/project loss, not just a
# bad migration). Supabase-managed schemas/extensions are infrastructure and are
# deliberately excluded so the dump can restore to ordinary PostgreSQL.
# Designed to run on a schedule:
# a Fly cron machine, a scheduled GitHub Actions workflow, or any host with the
# Postgres client tools + awscli installed.
#
# Required env:
#   DATABASE_URL_SYNC    libpq/psycopg URL (postgresql+psycopg://… or postgresql://…)
#   BACKUP_S3_BUCKET     destination, e.g. s3://muddato-backups/db
# Optional env:
#   BACKUP_S3_ENDPOINT   custom S3 endpoint (Cloudflare R2 / Backblaze B2 / etc.)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   S3 credentials
#   PG_DUMP_BIN          explicit pg_dump executable (CI pins PostgreSQL 17)
#
# Restore procedure + RPO/RTO: see docs/RUNBOOK.md.
set -euo pipefail

: "${DATABASE_URL_SYNC:?DATABASE_URL_SYNC is required}"

# pg_dump speaks libpq — strip any SQLAlchemy "+psycopg"/"+driver" suffix.
pg_url="$(printf '%s' "$DATABASE_URL_SYNC" | sed -E 's#postgresql\+[a-z0-9]+://#postgresql://#')"
dump_bin="${PG_DUMP_BIN:-pg_dump}"
command -v "$dump_bin" >/dev/null 2>&1 || {
  echo "[backup] pg_dump executable not found: $dump_bin" >&2
  exit 1
}
echo "[backup] using: $("$dump_bin" --version)"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
outfile="muddato-${ts}.dump"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
dump_path="${tmp}/${outfile}"

echo "[backup] pg_dump → ${dump_path}"
"$dump_bin" --format=custom \
  --schema=public \
  --no-owner \
  --no-privileges \
  --dbname="$pg_url" \
  --file="$dump_path"
echo "[backup] dump size: $(du -h "$dump_path" | cut -f1)"

if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then
  dest="${BACKUP_S3_BUCKET%/}/${outfile}"
  endpoint_arg=()
  [[ -n "${BACKUP_S3_ENDPOINT:-}" ]] && endpoint_arg=(--endpoint-url "$BACKUP_S3_ENDPOINT")
  echo "[backup] uploading → ${dest}"
  aws "${endpoint_arg[@]}" s3 cp "$dump_path" "$dest"
  echo "[backup] done: ${dest}"
else
  # No S3 configured: copy out of the temp dir so the operator can retrieve it.
  keep="./${outfile}"
  cp "$dump_path" "$keep"
  echo "[backup] dump saved to ${keep} — collect it for the restore drill (docs/RUNBOOK.md 2.2.2)"
fi
