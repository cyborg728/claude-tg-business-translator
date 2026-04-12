#!/usr/bin/env bash
# Export RabbitMQ definitions (exchanges / queues / bindings / users).
# This is a DEFINITIONS-only backup — use the built-in shovel / export for
# full message backups if needed.
#
# Usage:
#   RABBITMQ_MGMT_URL=http://localhost:15672 \
#   RABBITMQ_USER=guest RABBITMQ_PASS=guest \
#   ./scripts/backup_rabbitmq.sh [dest_dir]
set -euo pipefail

: "${RABBITMQ_MGMT_URL:=http://localhost:15672}"
: "${RABBITMQ_USER:=guest}"
: "${RABBITMQ_PASS:=guest}"

DEST_DIR="${1:-backups}"
mkdir -p "$DEST_DIR"

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$DEST_DIR/rabbitmq-$TS.json"

echo "Exporting $RABBITMQ_MGMT_URL/api/definitions → $OUT"
curl -sSf -u "$RABBITMQ_USER:$RABBITMQ_PASS" \
    "$RABBITMQ_MGMT_URL/api/definitions" > "$OUT"
gzip -9 "$OUT"

find "$DEST_DIR" -maxdepth 1 -name 'rabbitmq-*.json.gz' -mtime +30 -delete || true

echo "Done: $OUT.gz"
