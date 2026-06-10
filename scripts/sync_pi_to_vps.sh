#!/usr/bin/env bash
# Sync Pi scraping progress to VPS orchestrator every 5 minutes.
#
# Läuft auf dem Raspberry Pi als Hintergrundprozess (zusammen mit worker.py).
# Voraussetzung: SSH-Key für pit@187.124.181.116 ist konfiguriert.
#
# Starten:
#   nohup bash ~/fide-scraper/scripts/sync_pi_to_vps.sh > /tmp/sync_pi.log 2>&1 &
#
# Log prüfen:
#   tail -f /tmp/sync_pi.log

set -uo pipefail

VPS="pit@187.124.181.116"
PI_DB="${ORCHESTRATOR_DATA_DIR:-$HOME/fide-scraper/orchestrator/pi_data}/scraper.db"
INTERVAL=300   # 5 Minuten

echo "$(date): sync_pi_to_vps.sh gestartet. PI_DB=$PI_DB"

while true; do
    START=$(date +%s)

    python3 -c "import sqlite3; sqlite3.connect('$PI_DB').execute('PRAGMA wal_checkpoint(TRUNCATE)')" 2>&1 \
        && echo "$(date): WAL checkpoint ok" \
        || echo "$(date): WARNUNG: WAL checkpoint fehlgeschlagen"

    if scp -q "$PI_DB" "$VPS:/tmp/scraper_pi.db" 2>&1; then
        echo "$(date): SCP ok"
        if ssh "$VPS" "docker exec orchestrator-worker-1 \
            python3 /app/orchestrator/merge_pi_status.py \
            --pi-db /tmp/scraper_pi.db --vps-db /data/scraper.db" 2>&1; then
            echo "$(date): Merge ok"
        else
            echo "$(date): FEHLER: merge_pi_status.py fehlgeschlagen"
        fi
    else
        echo "$(date): FEHLER: SCP fehlgeschlagen — Retry in ${INTERVAL}s"
    fi

    ELAPSED=$(( $(date +%s) - START ))
    SLEEP=$(( INTERVAL - ELAPSED ))
    [ "$SLEEP" -gt 0 ] && sleep "$SLEEP"
done
