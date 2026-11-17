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
# Cloud-Kopie (seit 11/2026, Muster tunnelbliq ops/backup_db_vps.sh) in den Infomaniak
# Object Storage, Projekt elo-backup, per rclone (Remotes aus
# scripts/setup_cloud_backup_keys.sh, ~/.config/rclone/rclone.conf, Rechte 600):
#   elo-s3:elo-backup/daily/     jeder Dump, Rotation 14 Tage
#   elo-s3:elo-backup/monthly/   ein Dump je Monat (erster erfolgreicher Lauf), nie gelöscht
#   elo-crypt:latest/            config/ verschlüsselt (rclone crypt), jede Nacht abgeglichen
#   elo-crypt:monthly/YYYY-MM/   config/ zusätzlich einmal je Monat
# Alerting: eigener Healthchecks.io-Check, Ping-URL in
# ~/.config/fide-scraper/backup_healthcheck_url (600, nicht im Repo). /start zu Beginn,
# Erfolg mit Dateiname/Grösse, /fail bei jedem Fehler – auch bei unerwartetem Abbruch (ERR-Trap).
# Ohne Ping-URL läuft das Backup trotzdem, nur ohne Alarm. CLOUD_BACKUP=0 überspringt die Cloud.
#
# Cron (läuft direkt aus dem Checkout, Änderungen kommen per git pull):
#   45 3 * * * FIDE_DB_CONTAINER=fide-db /opt/fide-scraper/scripts/backup_fide_vps.sh
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
                           # behalten; Langzeit-Historie liegt in der Cloud (monthly/) und auf dem Mac
LOG="${BACKUP_DIR}/backup.log"
CLOUD_BACKUP="${CLOUD_BACKUP:-1}"
RCLONE_REMOTE="${RCLONE_REMOTE:-elo-s3:elo-backup}"
RCLONE_CRYPT="${RCLONE_CRYPT:-elo-crypt:}"
CLOUD_DAILY_RETENTION="${CLOUD_DAILY_RETENTION:-14d}"
HC_URL_FILE="${HC_URL_FILE:-$HOME/.config/fide-scraper/backup_healthcheck_url}"

mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date -u +%Y-%m-%dT%H%M%SZ)

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $*" >> "$LOG"; }

# Ping-URL enthält die Check-UUID → nicht im Repo.
HC_URL="${HC_URL:-$(cat "$HC_URL_FILE" 2>/dev/null || true)}"
[ -n "$HC_URL" ] || log "WARNUNG: keine Ping-URL in $HC_URL_FILE – Backup läuft ohne Alerting"

# hc <suffix> [nachricht]: "" = Erfolg, /start, /fail. Ein fehlgeschlagener Ping bricht nie ab.
hc() {
    [ -n "$HC_URL" ] || return 0
    curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "${2:-}" "${HC_URL}$1" \
        || log "WARNUNG: Healthchecks-Ping '${1:-success}' fehlgeschlagen"
}

fail() { log "FEHLER: $*"; hc /fail "$*"; exit 1; }

# set -e bricht sonst still ab – genau so lief das Backup 22.07.–24.09.2026 ohne Meldung ins
# Leere. Jeder unerwartete Abbruch landet so im Log und alarmiert.
trap 'msg="unerwarteter Abbruch in Zeile ${LINENO} (Exit $?)"; log "FEHLER: $msg"; hc /fail "$msg"' ERR

hc /start

# ── PostgreSQL: fidedb (inkl. Schema "orchestrator") ─────────────────────
# Fester Name statt Muster-Suche: Die alte Suche nach '^timescaledb-' fand seit der
# Wiederherstellung am 22.07.2026 nichts mehr, und `grep` ohne Treffer beendete das
# Skript wegen pipefail, BEVOR es loggen konnte → zwei Monate lang kein Backup, ohne Meldung.
PG_CONTAINER="${FIDE_DB_CONTAINER:-fide-db}"   # Hostinger bis 02.10.2026: fide-tunnelbliq-shared-db
if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    fail "DB-Container '$PG_CONTAINER' läuft nicht"
fi

PG_DUMP="${BACKUP_DIR}/fidedb_${TIMESTAMP}.dump"
if docker exec "$PG_CONTAINER" pg_dump -U fide -d fidedb -Fc > "${PG_DUMP}.tmp"; then
    mv "${PG_DUMP}.tmp" "$PG_DUMP"
    SIZE=$(du -h "$PG_DUMP" | cut -f1)
    log "fidedb OK: $(basename "$PG_DUMP") (${SIZE})"
else
    rm -f "${PG_DUMP}.tmp"
    fail "pg_dump fidedb fehlgeschlagen"
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

WARNINGS=()
if [ -n "$CONFIG_MISSING" ]; then
    log "WARNUNG: Konfiguration unvollständig gesichert — fehlt:${CONFIG_MISSING} (gesichert:${CONFIG_OK:- nichts})"
    WARNINGS+=("Konfiguration unvollständig:${CONFIG_MISSING}")
else
    log "config OK:${CONFIG_OK}"
fi

# ── Cloud (Infomaniak Object Storage) ────────────────────────────────────
# Ein Fehler hier macht den lokalen Dump nicht ungültig: loggen, am Ende /fail + Exit 1.
NAME=$(basename "$PG_DUMP")
ERRORS=()
if [ "$CLOUD_BACKUP" = "1" ]; then
    if rclone copyto "$PG_DUMP" "${RCLONE_REMOTE}/daily/${NAME}" >> "$LOG" 2>&1; then
        log "Upload OK: daily/${NAME}"
    else
        log "FEHLER: Upload daily/${NAME} fehlgeschlagen"
        ERRORS+=("Upload daily/${NAME}")
    fi

    # Monats-Dump: nicht starr am 1., sondern solange für den laufenden Monat keiner im
    # Bucket liegt – ein verpasster Tag holt sich selbst nach. Listing fehlgeschlagen →
    # leer → Upload wird versucht (schlimmstenfalls doppelt, nie fehlend).
    MONTH=$(date -u +%Y-%m)
    MONTHLY_EXISTING=$(rclone lsf "${RCLONE_REMOTE}/monthly/" --include "fidedb_${MONTH}-*.dump" 2>> "$LOG" || true)
    if [ -n "$MONTHLY_EXISTING" ]; then
        :   # Monats-Dump schon vorhanden
    elif rclone copyto "$PG_DUMP" "${RCLONE_REMOTE}/monthly/${NAME}" >> "$LOG" 2>&1; then
        log "Upload OK: monthly/${NAME}"
        if rclone copy "$CONFIG_DIR" "${RCLONE_CRYPT}monthly/${MONTH}" >> "$LOG" 2>&1; then
            log "Upload OK: config verschlüsselt nach monthly/${MONTH}"
        else
            log "FEHLER: Upload config monthly/${MONTH} fehlgeschlagen"
            ERRORS+=("Upload config monthly/${MONTH}")
        fi
    else
        log "FEHLER: Upload monthly/${NAME} fehlgeschlagen"
        ERRORS+=("Upload monthly/${NAME}")
    fi

    # Konfiguration verschlüsselt, Stand dieser Nacht (sync: entfernt auch den Proxy-Marker,
    # wenn er lokal fehlt – wie oben).
    if rclone sync "$CONFIG_DIR" "${RCLONE_CRYPT}latest" >> "$LOG" 2>&1; then
        log "Upload OK: config verschlüsselt nach latest/"
    else
        log "FEHLER: Upload config latest/ fehlgeschlagen"
        ERRORS+=("Upload config latest/")
    fi

    # Rotation nur in daily/ – monthly/ bleibt dauerhaft.
    if ! rclone delete "${RCLONE_REMOTE}/daily" --min-age "$CLOUD_DAILY_RETENTION" >> "$LOG" 2>&1; then
        log "FEHLER: Cloud-Rotation daily/ fehlgeschlagen"
        ERRORS+=("Cloud-Rotation daily/")
    fi
else
    log "Cloud übersprungen (CLOUD_BACKUP=$CLOUD_BACKUP)"
fi

# Nicht als nacktes [ ... ] – das würde den ERR-Trap auslösen.
if [ ${#ERRORS[@]} -gt 0 ]; then
    MSG=$(printf '%s; ' "${ERRORS[@]}")
    hc /fail "Fehlgeschlagen: ${MSG%; } (lokaler Dump ${NAME} ok)"
    exit 1
fi
if [ ${#WARNINGS[@]} -gt 0 ]; then
    MSG=$(printf '%s; ' "${WARNINGS[@]}")
    hc /fail "${MSG%; } (Dump ${NAME} ${SIZE} ok)"
    exit 1
fi

hc "" "Backup OK: ${NAME} (${SIZE})"
