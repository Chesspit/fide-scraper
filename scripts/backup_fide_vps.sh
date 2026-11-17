#!/usr/bin/env bash
# fide-scraper — tägliches Backup auf dem VPS (Cron, siehe docs/project_status.md).
#
# Sichert fidedb (PostgreSQL/TimescaleDB, Container fide-db) via pg_dump -Fc.
# Seit Review #5 enthält der Dump auch die Orchestrator-Queue (Schema
# "orchestrator": scrape_groups/scrape_runs) — die frühere separate
# SQLite-Sicherung von scraper.db entfällt.
#
# Zusätzlich (Review F-04, Entscheid Pit 14.11.2026: unverschlüsselt, Rechte 600):
# die Betriebskonfiguration, die sonst nur auf dem VPS liegt → $BACKUP_DIR/config/
#   env                    Kopie von /opt/fide-scraper/.env (Secrets!)
#   runtime_settings.json  Thread-Schalter/Aktivzeiten aus dem Volume orchestrator_data
#   proxy_fallback_active  Marker „Proxy läuft auf Plan 2" (nur wenn vorhanden)
# Der Mac-Pull (pull_backup_macmini.sh) nimmt den Ordner per rsync mit.
# Ein Fehler hier bricht das Backup NICHT ab — der DB-Dump ist das Wichtigste.
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

BACKUP_DIR="${FIDE_BACKUP_DIR:-/home/pit/backups/fide-scraper}"
REPO_DIR="${FIDE_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
WORKER_CONTAINER="${FIDE_WORKER_CONTAINER:-infomaniak-worker-1}"
KEEP_DUMPS=3               # fidedb-Dumps sind groß (~1,7 GB, Nov. 2026) — nur die letzten 3
                           # behalten; Langzeit-Historie liegt offsite (Mac-Mini-Pull)
LOG="${BACKUP_DIR}/backup.log"

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date -u +%Y-%m-%dT%H%M%SZ)

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $*" >> "$LOG"; }

# ── PostgreSQL: fidedb (inkl. Schema "orchestrator") ─────────────────────
# Fester Name statt Muster-Suche: Die alte Suche nach '^timescaledb-' fand seit der
# Wiederherstellung am 22.07.2026 nichts mehr, und `grep` ohne Treffer beendete das
# Skript wegen pipefail, BEVOR es loggen konnte → zwei Monate lang kein Backup, ohne Meldung.
PG_CONTAINER="${FIDE_DB_CONTAINER:-fide-db}"   # Hostinger bis 02.10.2026: fide-tunnelbliq-shared-db
if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    log "FEHLER: DB-Container '$PG_CONTAINER' läuft nicht"
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

# ── Rotation: exakt die neuesten KEEP_DUMPS Dumps behalten ────────────────
ls -1t "$BACKUP_DIR"/fidedb_*.dump 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) | xargs -r rm -f

# ── Betriebskonfiguration (Review F-04) ──────────────────────────────────
# Je eine aktuelle Kopie, jede Nacht überschrieben. Fehler nur loggen.
CONFIG_DIR="${BACKUP_DIR}/config"
umask 077
mkdir -p "$CONFIG_DIR" && chmod 700 "$CONFIG_DIR"
CONFIG_OK=""
CONFIG_MISSING=""

if [ -f "${REPO_DIR}/.env" ] && cp "${REPO_DIR}/.env" "${CONFIG_DIR}/env.tmp" \
        && mv "${CONFIG_DIR}/env.tmp" "${CONFIG_DIR}/env"; then
    chmod 600 "${CONFIG_DIR}/env"
    CONFIG_OK="$CONFIG_OK env"
else
    rm -f "${CONFIG_DIR}/env.tmp"
    CONFIG_MISSING="$CONFIG_MISSING env"
fi

# Dateien aus dem Volume orchestrator_data (im Worker-Container unter /data).
# docker cp geht auch bei gestopptem Container. Der Proxy-Marker ist optional.
for f in runtime_settings.json proxy_fallback_active; do
    if docker cp "${WORKER_CONTAINER}:/data/${f}" "${CONFIG_DIR}/${f}.tmp" 2>/dev/null; then
        mv "${CONFIG_DIR}/${f}.tmp" "${CONFIG_DIR}/${f}"
        chmod 600 "${CONFIG_DIR}/${f}"
        CONFIG_OK="$CONFIG_OK $f"
    else
        rm -f "${CONFIG_DIR}/${f}.tmp"
        # Kein Marker heißt „Plan 1 aktiv": die alte Kopie wäre dann irreführend.
        if [ "$f" = "proxy_fallback_active" ]; then
            rm -f "${CONFIG_DIR}/${f}"
        else
            CONFIG_MISSING="$CONFIG_MISSING $f"
        fi
    fi
done

if [ -n "$CONFIG_MISSING" ]; then
    log "WARNUNG: Konfiguration unvollständig gesichert — fehlt:${CONFIG_MISSING} (gesichert:${CONFIG_OK:- nichts})"
else
    log "config OK:${CONFIG_OK}"
fi
