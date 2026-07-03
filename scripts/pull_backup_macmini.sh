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

mkdir -p "$DEST"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

if rsync -az --timeout=300 "${VPS}:/home/pit/backups/fide-scraper/" "$DEST/"; then
    find "$DEST" -name 'fidedb_*.dump'  -mtime +"$RETENTION_DAYS_PG"     -delete
    find "$DEST" -name 'scraperdb_*.db' -mtime +"$RETENTION_DAYS_SQLITE" -delete
    N_PG=$(find "$DEST" -name 'fidedb_*.dump' | wc -l | tr -d ' ')
    log "Pull OK: ${N_PG} fidedb-Dumps lokal, $(du -sh "$DEST" | cut -f1) gesamt"
else
    log "FEHLER: rsync vom VPS fehlgeschlagen (Exit $?)"
    exit 1
fi
