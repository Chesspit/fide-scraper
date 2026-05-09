#!/usr/bin/env bash
# Lokaler Backfill vom Mac Mini.
# Verhindert Sleep, startet Tunnel, startet Backfill mit Retry bei Absturz.
#
# Verwendung:
#   bash scripts/run_local_backfill.sh global_02
#   bash scripts/run_local_backfill.sh global_02 2013-01-01 2026-03-01

set -uo pipefail

GROUP=${1:?Gruppenname angeben, z.B.: bash run_local_backfill.sh global_02}
FROM=${2:-2013-01-01}
TO=${3:-2026-03-01}
DB_URL="postgresql://fide:nimzo194.@localhost:5434/fidedb"
LOG="/tmp/backfill_${GROUP}_local.log"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "$(date): Starte lokalen Backfill: group=$GROUP from=$FROM to=$TO"
echo "Log: $LOG"

# Tunnel sicherstellen
if ! lsof -i :5434 | grep -q LISTEN; then
    echo "$(date): Tunnel nicht aktiv — starte tunnel.sh..."
    bash "$SCRIPT_DIR/scripts/tunnel.sh" &
    sleep 5
fi

# Mac Mini wachhalten + Backfill starten (caffeinate verhindert Sleep)
# Bei Absturz automatisch neu starten
echo "$(date): caffeinate aktiv — Mac Mini bleibt wach"
while true; do
    caffeinate -i env DATABASE_URL="$DB_URL" \
        python3 "$SCRIPT_DIR/scripts/backfill.py" \
        --from "$FROM" --to "$TO" \
        --group "$GROUP" --reverse \
        >> "$LOG" 2>&1

    EXIT=$?

    # Prüfen ob normal beendet
    if tail -1 "$LOG" 2>/dev/null | grep -q "Backfill complete"; then
        echo "$(date): Backfill abgeschlossen."
        break
    fi

    if [ $EXIT -eq 0 ]; then
        echo "$(date): Prozess beendet (exit 0, aber kein 'Backfill complete'). Fertig."
        break
    fi

    echo "$(date): Prozess abgestürzt (exit $EXIT) — Neustart in 15s..."
    sleep 15

    # Tunnel neu prüfen
    if ! lsof -i :5434 | grep -q LISTEN; then
        echo "$(date): Tunnel weg — starte tunnel.sh neu..."
        bash "$SCRIPT_DIR/scripts/tunnel.sh" &
        sleep 10
    fi
done

echo "$(date): run_local_backfill.sh beendet."
