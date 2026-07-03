#!/usr/bin/env bash
# fide-scraper — tägliches Backup auf dem VPS (Cron, siehe docs/project_status.md).
#
# Sichert beide Datenbestände des Projekts:
#   1. fidedb (PostgreSQL/TimescaleDB, Coolify-Container) via pg_dump -Fc
#   2. scraper.db (Orchestrator-Queue, Docker-Volume orchestrator_orchestrator_data)
#      via SQLite-Online-Backup-API — WAL-sicher, blockiert laufende Worker nicht
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
#
# RESTORE scraper.db: Datei einfach zurück ins Volume kopieren
#   (docker cp scraperdb_*.db orchestrator-worker-1:/data/scraper.db) + Worker-Neustart.
set -euo pipefail

BACKUP_DIR="/home/pit/backups/fide-scraper"
RETENTION_DAYS_PG=7        # fidedb-Dumps sind groß — 7 Tage reichen
RETENTION_DAYS_SQLITE=30   # scraper.db ist klein — großzügige Historie
LOG="${BACKUP_DIR}/backup.log"

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date -u +%Y-%m-%dT%H%M%SZ)

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $*" >> "$LOG"; }

# ── 1. PostgreSQL: fidedb ────────────────────────────────────────────────
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

# ── 2. SQLite: scraper.db ────────────────────────────────────────────────
# Über einen Container, der das /data-Volume gemountet hat (Worker bevorzugt,
# Dashboard als Fallback falls der Worker gerade neu startet).
SQLITE_CONTAINER=""
for c in orchestrator-worker-1 orchestrator-dashboard-1; do
    if docker ps --format '{{.Names}}' | grep -qx "$c"; then
        SQLITE_CONTAINER="$c"
        break
    fi
done

if [ -z "$SQLITE_CONTAINER" ]; then
    log "FEHLER: kein Orchestrator-Container mit /data-Volume läuft — scraper.db nicht gesichert"
    exit 1
fi

SQLITE_FILE="${BACKUP_DIR}/scraperdb_${TIMESTAMP}.db"
if docker exec "$SQLITE_CONTAINER" python3 -c "
import sqlite3
src = sqlite3.connect('file:/data/scraper.db?mode=ro', uri=True)
dst = sqlite3.connect('/data/scraper_backup_tmp.db')
src.backup(dst)
dst.close()
src.close()
"; then
    docker cp -q "${SQLITE_CONTAINER}:/data/scraper_backup_tmp.db" "${SQLITE_FILE}.tmp"
    docker exec "$SQLITE_CONTAINER" rm -f /data/scraper_backup_tmp.db
    mv "${SQLITE_FILE}.tmp" "$SQLITE_FILE"
    log "scraper.db OK: $(basename "$SQLITE_FILE") ($(du -h "$SQLITE_FILE" | cut -f1)) via ${SQLITE_CONTAINER}"
else
    docker exec "$SQLITE_CONTAINER" rm -f /data/scraper_backup_tmp.db 2>/dev/null || true
    log "FEHLER: SQLite-Backup fehlgeschlagen (Container: ${SQLITE_CONTAINER})"
    exit 1
fi

# ── 3. Rotation ──────────────────────────────────────────────────────────
find "$BACKUP_DIR" -name 'fidedb_*.dump'  -mtime +"$RETENTION_DAYS_PG"     -delete
find "$BACKUP_DIR" -name 'scraperdb_*.db' -mtime +"$RETENTION_DAYS_SQLITE" -delete
