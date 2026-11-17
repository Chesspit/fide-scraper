#!/usr/bin/env bash
# fide-scraper — Offsite-Kopie der VPS-Backups auf den Mac Mini.
#
# Läuft via launchd (net.chesspit.fide-backup-pull, siehe
# scripts/net.chesspit.fide-backup-pull.plist): RunAtLoad bei jedem Boot
# (der Mac Mini fährt nachts komplett runter statt zu schlafen, daher greift
# ein reines StartCalendarInterval nicht zuverlässig) + 09:30 als Fallback.
#
# Zieht /home/pit/backups/fide-scraper/ vom VPS (Dumps + backup.log + config/ mit
# .env-Kopie und Thread-Einstellungen, Review F-04) und zusätzlich die
# Parquet-Caches des Frontends direkt aus dem Checkout (einzige vollständige
# Kopie liegt auf dem VPS; Entscheid Pit 14.11.2026: direkt per rsync, ohne
# zweite Kopie auf dem Server). Die Dumps werden
# lokal knapper rotiert als auf dem VPS (Mac-Platte ist kleiner): die VPS-Seite
# ist das primäre Backup, diese Kopie schützt gegen Totalverlust des VPS.
set -euo pipefail

. "$(cd "$(dirname "$0")" && pwd)/vps.env"   # FIDE_VPS, FIDE_VPS_COMPOSE_DIR
VPS="$FIDE_VPS"
DEST="$HOME/backups/fide-scraper/vps"
LOG="$HOME/backups/fide-scraper/pull.log"
RETENTION_DAYS_PG=5        # ~9 GB bei 1,7-GB-Dumps (Nov. 2026)
# Checkout auf dem VPS: FIDE_VPS_COMPOSE_DIR ist <repo>/deploy/infomaniak
VPS_REPO="${FIDE_VPS_COMPOSE_DIR%/deploy/*}"
PARQUET_DEST="$DEST/parquet"
KEEP_MIN=2                 # diese jeweils neuesten Dateien nie löschen, egal wie alt
                           # (schützt vor Totalverlust nach Urlaub/langem Mac-Aus, falls
                           # der VPS in der Zwischenzeit seine eigene 7-Tage-Rotation
                           # weiterdreht und hier lange kein erfolgreicher Pull lief)

mkdir -p "$DEST"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# Altersbasiert löschen, aber die KEEP_MIN neuesten Dateien (Name = ISO-Timestamp,
# alphabetisch sortierbar) grundsätzlich von der Löschung ausnehmen.
# Kein mapfile/Array: macOS liefert nur Bash 3.2 (aus Lizenzgründen), das kennt
# beides nicht — daher reines Zeilen-Counting + head.
prune_keep_min() {
    local pattern="$1" max_age_days="$2"
    local files n prunable
    files=$(find "$DEST" -name "$pattern" | sort)
    [ -z "$files" ] && return
    n=$(printf '%s\n' "$files" | grep -c .)
    prunable=$(( n - KEEP_MIN ))
    (( prunable <= 0 )) && return
    printf '%s\n' "$files" | head -n "$prunable" | while IFS= read -r f; do
        find "$f" -mtime +"$max_age_days" -delete
    done
}

if rsync -az --timeout=300 "${VPS}:/home/pit/backups/fide-scraper/" "$DEST/"; then
    prune_keep_min 'fidedb_*.dump'  "$RETENTION_DAYS_PG"
    N_PG=$(find "$DEST" -name 'fidedb_*.dump' | wc -l | tr -d ' ')
    # Ein erfolgreiches rsync heißt nicht, dass der VPS noch sichert (Juli–Sept. 2026
    # kam zwei Monate lang derselbe alte Dump) → Alter des neuesten Dumps prüfen.
    if [ -z "$(find "$DEST" -name 'fidedb_*.dump' -mtime -2)" ]; then
        log "WARNUNG: neuester fidedb-Dump älter als 2 Tage — Backup auf dem VPS prüfen (backup.log)"
        exit 1
    fi
    # config/ enthält die .env des Servers — nur für diesen Benutzer lesbar halten.
    [ -d "$DEST/config" ] && chmod -R go-rwx "$DEST/config"

    # Parquet-Caches: eine aktuelle Fassung, rsync überträgt nur Geändertes.
    # Ein Fehler hier ist kein Backup-Ausfall (die Dumps sind da) → nur warnen.
    mkdir -p "$PARQUET_DEST"
    if rsync -az --timeout=300 --include='*.parquet' --exclude='*' \
            "${VPS}:${VPS_REPO}/frontend/" "$PARQUET_DEST/"; then
        PARQUET_INFO="$(find "$PARQUET_DEST" -name '*.parquet' | wc -l | tr -d ' ') Parquet-Caches"
    else
        PARQUET_INFO="Parquet-Caches NICHT aktualisiert"
        log "WARNUNG: rsync der Parquet-Caches fehlgeschlagen (${VPS}:${VPS_REPO}/frontend/)"
    fi
    [ -f "$DEST/config/env" ] || log "WARNUNG: config/env fehlt im Backup — backup_fide_vps.sh auf dem VPS aktuell?"

    log "Pull OK: ${N_PG} fidedb-Dumps lokal, ${PARQUET_INFO}, $(du -sh "$DEST" | cut -f1) gesamt"
else
    log "FEHLER: rsync vom VPS fehlgeschlagen (Exit $?)"
    exit 1
fi
