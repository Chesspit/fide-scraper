#!/usr/bin/env bash
# fide-scraper — tägliches Backup auf dem VPS (Cron, siehe docs/project_status.md).
#
# Sichert fidedb (PostgreSQL/TimescaleDB, Coolify-Container) via pg_dump -Fc.
# Seit Review #5 enthält der Dump auch die Orchestrator-Queue (Schema
# "orchestrator": scrape_groups/scrape_runs) — die frühere separate
# SQLite-Sicherung von scraper.db entfällt.
#
# Muster analog /home/pit/backups/tunnelbliq/backup_db_vps.sh.
# Deploy: scp scripts/backup_fide_vps.sh pit@VPS:/home/pit/backups/fide-scraper/
# Cron:   45 3 * * * /home/pit/backups/fide-scraper/backup_fide_vps.sh
#
# RESTORE fidedb (TimescaleDB — Reihenfolge wichtig, sonst kaputte Hypertables):
#   docker exec -i <timescaledb-container> psql -U fide -d fidedb_neu -c \
#       "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();"
#   docker exec -i <timescaledb-container> pg_restore -U fide -d fidedb_neu --no-owner < dump
#   docker exec -i <timescaledb-container> psql -U fide -d fidedb_neu -c \
#       "SELECT timescaledb_post_restore();"
#   (Die pg_dump-Warnungen zu zirkulären FKs auf hypertable/chunk sind normal.)
#   Nur die Queue wiederherstellen: pg_restore --schema=orchestrator …
set -euo pipefail

BACKUP_DIR="/home/pit/backups/fide-scraper"
RETENTION_DAYS_PG=7        # fidedb-Dumps sind groß — 7 Tage reichen
LOG="${BACKUP_DIR}/backup.log"

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date -u +%Y-%m-%dT%H%M%SZ)

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $*" >> "$LOG"; }

# ── PostgreSQL: fidedb (inkl. Schema "orchestrator") ─────────────────────
PG_CONTAINER=$(docker ps --format '{{.Names}}' | grep '^timescaledb-' | head -1)
if [ -z "$PG_CONTAINER" ]; then
    log "FEHLER: kein timescaledb-Container gefunden"
    exit 1
fi

PG_DUMP="${BACKUP_DIR}/fidedb_${TIMESTAMP}.dump"
if docker exec "$PG_CONTAINER" pg_dump -U fide -d fidedb -Fc > "${PG_DUMP}.tmp"; then
    mv "${PG_DUMP}.tmp" "$PG_DUMP"
    log "fidedb OK: $(basename "$PG_DUMP") ($(du -h "$PG_DUMP" | cut -f1))"
else
    rm -f "${PG_DUMP}.tmp"
    log "FEHLER: pg_dump fidedb fehlgeschlagen"
    exit 1
fi

# ── Rotation ─────────────────────────────────────────────────────────────
find "$BACKUP_DIR" -name 'fidedb_*.dump'  -mtime +"$RETENTION_DAYS_PG"     -delete
# Alte scraper.db-Sicherungen (vor Review #5) laufen über die Zeit aus:
find "$BACKUP_DIR" -name 'scraperdb_*.db' -mtime +30 -delete
