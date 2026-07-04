#!/usr/bin/env bash
# fide-scraper — Offsite-Kopie der VPS-Backups auf den Mac Mini.
#
# Läuft täglich 07:30 via launchd (net.chesspit.fide-backup-pull, siehe
# scripts/net.chesspit.fide-backup-pull.plist) — launchd holt einen wegen
# Schlafmodus verpassten Lauf nach dem Aufwachen nach, cron nicht.
#
# Zieht /home/pit/backups/fide-scraper/ vom VPS (Dumps + backup.log) und
# rotiert lokal knapper als der VPS (Mac-Platte ist kleiner): die VPS-Seite
# ist das primäre Backup, diese Kopie schützt gegen Totalverlust des VPS.
set -euo pipefail

VPS="pit@187.124.181.116"
DEST="$HOME/backups/fide-scraper/vps"
LOG="$HOME/backups/fide-scraper/pull.log"
RETENTION_DAYS_PG=5        # ~4,3 GB bei 854-MB-Dumps
RETENTION_DAYS_SQLITE=30
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
    prune_keep_min 'scraperdb_*.db' "$RETENTION_DAYS_SQLITE"
    N_PG=$(find "$DEST" -name 'fidedb_*.dump' | wc -l | tr -d ' ')
    log "Pull OK: ${N_PG} fidedb-Dumps lokal, $(du -sh "$DEST" | cut -f1) gesamt"
else
    log "FEHLER: rsync vom VPS fehlgeschlagen (Exit $?)"
    exit 1
fi
