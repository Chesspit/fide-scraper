#!/usr/bin/env bash
# Automatische Kette für alle pending female_XXXX_YY Gruppen.
# Wartet auf den laufenden Backfill (via PID-Datei), dann seed → backfill → DB-Update.
#
# Verwendung:
#   bash scripts/run_female_chain.sh [STARTGRUPPE]
#
# Ohne Argument: startet bei female_2000_01 (female_2100_06 läuft bereits).

set -uo pipefail

START_GROUP="${1:-female_2000_01}"
FROM="2010-01-01"
TO="2026-04-01"
DB_URL="postgresql://fide:nimzo194.@localhost:5434/fidedb"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHAIN_LOG="/tmp/female_chain.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$CHAIN_LOG"
}

# Warten auf Prozess via PID-Datei (robust, kein pgrep-Name-Matching)
wait_for_pid_file() {
    local PID_FILE="$1"
    local GROUP_NAME="$2"
    local LOG_FILE="$3"

    if [ ! -f "$PID_FILE" ]; then
        log "Keine PID-Datei für $GROUP_NAME — überspringe Warte-Phase."
        return
    fi

    local PID
    PID=$(cat "$PID_FILE")
    log "Warte auf $GROUP_NAME (PID $PID)..."

    while kill -0 "$PID" 2>/dev/null; do
        # Fortschritt anzeigen
        local LAST
        LAST=$(tail -1 "$LOG_FILE" 2>/dev/null | grep -oE '\[[0-9]+/[0-9]+\]' | head -1 || true)
        log "  $GROUP_NAME läuft noch... $LAST"
        sleep 60
    done

    log "$GROUP_NAME (PID $PID) beendet."
    rm -f "$PID_FILE"
}

# Alle pending female Gruppen in aufsteigender Reihenfolge (hohe ELO zuerst)
ALL_GROUPS=(
    female_2000_01 female_2000_02 female_2000_03 female_2000_04 female_2000_05
    female_2000_06 female_2000_07 female_2000_08 female_2000_09
    female_1900_01 female_1900_02 female_1900_03 female_1900_04 female_1900_05
    female_1900_06 female_1900_07 female_1900_08 female_1900_09 female_1900_10
    female_1900_11 female_1900_12 female_1900_13 female_1900_14 female_1900_15
    female_1900_16
    female_1800_01 female_1800_02 female_1800_03 female_1800_04 female_1800_05
    female_1800_06 female_1800_07 female_1800_08 female_1800_09 female_1800_10
    female_1800_11 female_1800_12 female_1800_13 female_1800_14 female_1800_15
    female_1800_16 female_1800_17 female_1800_18 female_1800_19 female_1800_20
    female_1800_21 female_1800_22 female_1800_23 female_1800_24
)

# Auf female_2100_06 warten (läuft gerade)
wait_for_pid_file \
    "/tmp/backfill_female_2100_06.pid" \
    "female_2100_06" \
    "/tmp/backfill_female_2100_06_local.log"

# DB-Status für female_2100_06 updaten
python3 -c "
import psycopg2
conn = psycopg2.connect('$DB_URL')
cur = conn.cursor()
cur.execute(\"UPDATE groups SET backfill_status='complete', scraped_from='$FROM', scraped_to='$TO' WHERE group_name='female_2100_06' AND backfill_status != 'complete'\")
conn.commit()
print(f'female_2100_06 DB: {cur.rowcount} Zeile(n) aktualisiert')
conn.close()
" 2>&1 | tee -a "$CHAIN_LOG"

# Startindex bestimmen
START_IDX=0
for i in "${!ALL_GROUPS[@]}"; do
    if [ "${ALL_GROUPS[$i]}" = "$START_GROUP" ]; then
        START_IDX=$i
        break
    fi
done

TOTAL=${#ALL_GROUPS[@]}
log "Starte Kette ab Index $START_IDX: $START_GROUP ($TOTAL Gruppen in Liste)"

# Kette durchlaufen — STRIKT SEQUENZIELL (warten bis jede Gruppe fertig ist)
for i in "${!ALL_GROUPS[@]}"; do
    if [ "$i" -lt "$START_IDX" ]; then continue; fi

    GROUP="${ALL_GROUPS[$i]}"
    GROUP_LOG="/tmp/backfill_${GROUP}_local.log"
    POS="[$((i+1))/$TOTAL]"

    # Prüfen ob schon complete
    STATUS=$(python3 -c "
import psycopg2
conn = psycopg2.connect('$DB_URL')
cur = conn.cursor()
cur.execute(\"SELECT backfill_status FROM groups WHERE group_name='$GROUP'\")
row = cur.fetchone()
conn.close()
print(row[0] if row else 'unknown')
" 2>/dev/null)

    if [ "$STATUS" = "complete" ]; then
        log "$POS $GROUP bereits complete — überspringe."
        continue
    fi

    log "$POS Starte $GROUP..."

    # Tunnel sicherstellen
    if ! lsof -i :5434 | grep -q LISTEN 2>/dev/null; then
        log "  Tunnel nicht aktiv — starte tunnel.sh..."
        bash "$SCRIPT_DIR/scripts/tunnel.sh" &
        sleep 10
    fi

    # Spieler seeden
    log "  Seede $GROUP..."
    python3 "$SCRIPT_DIR/scripts/seed_players.py" --group "$GROUP" 2>&1 | tee -a "$CHAIN_LOG"

    # Backfill SYNCHRON (warten bis fertig, mit Auto-Restart bei Absturz)
    log "  Backfill $GROUP ($FROM → $TO)..."
    rm -f "$GROUP_LOG"

    while true; do
        caffeinate -i env DATABASE_URL="$DB_URL" \
            python3 "$SCRIPT_DIR/scripts/backfill.py" \
            --from "$FROM" --to "$TO" \
            --group "$GROUP" --reverse \
            >> "$GROUP_LOG" 2>&1
        EXIT=$?

        if tail -3 "$GROUP_LOG" 2>/dev/null | grep -q "Backfill complete"; then
            log "  $GROUP abgeschlossen ✓"
            break
        fi

        if [ $EXIT -eq 0 ]; then
            log "  $GROUP exit 0 ohne 'Backfill complete' — nehme als fertig an."
            break
        fi

        log "  $GROUP Absturz (exit $EXIT) — Neustart in 15s..."
        sleep 15

        # Tunnel-Check
        if ! lsof -i :5434 | grep -q LISTEN 2>/dev/null; then
            log "  Tunnel weg — starte neu..."
            bash "$SCRIPT_DIR/scripts/tunnel.sh" &
            sleep 10
        fi
    done

    # DB-Status updaten
    python3 -c "
import psycopg2
conn = psycopg2.connect('$DB_URL')
cur = conn.cursor()
cur.execute(\"UPDATE groups SET backfill_status='complete', scraped_from='$FROM', scraped_to='$TO' WHERE group_name='$GROUP'\")
conn.commit()
print(f'  DB: {cur.rowcount} Zeile(n) aktualisiert → complete')
conn.close()
" 2>&1 | tee -a "$CHAIN_LOG"

done

log "=== Alle female-Gruppen abgeschlossen! ==="
