#!/bin/bash
# Läuft alle noch ausstehenden global_XX Gruppen sequentiell durch.
# Caffeinate hält den Mac Mini die ganze Zeit wach.
# Verwendung: caffeinate -dim bash scripts/run_global_chain.sh
#
# Gruppen werden übersprungen falls bereits complete.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

FROM_DATE="2012-08-01"
TO_DATE=$(python3 -c "from datetime import date; t=date.today(); m=t.month-1 or 12; y=t.year if t.month>1 else t.year-1; print(date(y,m,1))")

GROUPS=(global_26b global_27a global_27b global_28a global_28b)

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# Tunnel-Check
check_tunnel() {
    python3 -c "
import psycopg2, sys
try:
    conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
    conn.close()
except Exception as e:
    print(f'Tunnel fehlt: {e}'); sys.exit(1)
" 2>&1
}

# Warten bis eine Gruppe fertig ist
wait_for_completion() {
    local GROUP="$1"
    log "Warte auf Fertigstellung von $GROUP..."
    while true; do
        MISSING=$(python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor()
cur.execute(\"\"\"
    SELECT COUNT(*) FROM players p WHERE p.analysis_group='$GROUP'
    AND NOT EXISTS (
        SELECT 1 FROM scrape_periods sp
        WHERE sp.fide_id=p.fide_id AND sp.period='2026-03-01'
    )
\"\"\")
print(cur.fetchone()[0])
conn.close()
" 2>/dev/null)
        ERRORS=$(python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor()
cur.execute(\"\"\"
    SELECT COUNT(*) FILTER (WHERE status='error')
    FROM scrape_periods sp JOIN players p ON p.fide_id=sp.fide_id
    WHERE p.analysis_group='$GROUP'
\"\"\")
print(cur.fetchone()[0])
conn.close()
" 2>/dev/null)
        if [ "$MISSING" = "0" ] && [ "$ERRORS" = "0" ]; then
            log "$GROUP: fertig ✓"
            return 0
        fi
        log "$GROUP: $MISSING Spieler ohne letzte Periode, $ERRORS Fehler — warte 60s..."
        sleep 60
    done
}

# Gruppe als complete markieren
mark_complete() {
    local GROUP="$1"
    python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor()
cur.execute(\"UPDATE groups SET backfill_status='complete', scraped_from='$FROM_DATE', scraped_to='$TO_DATE' WHERE group_name='$GROUP'\")
conn.commit()
print('$GROUP → complete')
conn.close()
"
}

# Status einer Gruppe prüfen
group_status() {
    python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor()
cur.execute(\"SELECT backfill_status FROM groups WHERE group_name='$1'\")
r = cur.fetchone()
print(r[0] if r else 'unknown')
conn.close()
"
}

log "======================================================="
log "Global Chain: ${GROUPS[*]}"
log "caffeinate hält Mac Mini wach"
log "======================================================="

check_tunnel

for GROUP in "${GROUPS[@]}"; do
    STATUS=$(group_status "$GROUP")

    if [ "$STATUS" = "complete" ]; then
        log "$GROUP bereits complete — übersprungen"
        continue
    fi

    log "--- $GROUP (ELO $(python3 -c "
import psycopg2; conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor(); cur.execute(\"SELECT elo_min, elo_max FROM groups WHERE group_name='$GROUP'\")
r = cur.fetchone(); print(f'{r[0]}-{r[1]}' if r else '?'); conn.close()
")) ---"

    # Spieler seeden falls noch nicht geschehen
    SPIELER=$(python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor()
cur.execute(\"SELECT COUNT(*) FROM players WHERE analysis_group='$GROUP'\")
print(cur.fetchone()[0])
conn.close()
")
    if [ "$SPIELER" = "0" ]; then
        log "Seede $GROUP..."
        python3 scripts/seed_players.py --group "$GROUP"
    else
        log "$GROUP bereits geseeded ($SPIELER Spieler)"
    fi

    # Backfill starten (im Hintergrund, damit wir es überwachen können)
    log "Starte Backfill $GROUP ($FROM_DATE → $TO_DATE)..."
    python3 -m scraper.main run \
        --group "$GROUP" \
        --from "$FROM_DATE" \
        --to "$TO_DATE" &
    BACKFILL_PID=$!

    # Warten bis fertig
    wait_for_completion "$GROUP"

    # Hintergrundprozess beenden falls noch läuft
    kill "$BACKFILL_PID" 2>/dev/null || true
    wait "$BACKFILL_PID" 2>/dev/null || true

    mark_complete "$GROUP"
    log "$GROUP abgeschlossen ✓"
    log ""
done

log "======================================================="
log "ALLE GRUPPEN FERTIG — ELO >= 2300 komplett gescrapt!"
log "======================================================="
