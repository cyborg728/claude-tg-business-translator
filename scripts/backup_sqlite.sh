#!/usr/bin/env bash
# Local SQLite backup — mirrors what k8s/base/backup-cronjobs.yaml does in-cluster.
# Usage: ./scripts/backup_sqlite.sh [source.db] [dest_dir]
set -euo pipefail

DB_PATH="${1:-${DATABASE_PATH:-data/bot.db}}"
DEST_DIR="${2:-backups}"

mkdir -p "$DEST_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$DEST_DIR/sqlite-$TS.db"

echo "Backing up $DB_PATH → $OUT"
sqlite3 "$DB_PATH" ".backup '$OUT'"
gzip -9 "$OUT"

# Keep the 14 most recent daily snapshots
find "$DEST_DIR" -maxdepth 1 -name 'sqlite-*.db.gz' -mtime +14 -delete || true

echo "Done: $OUT.gz"
