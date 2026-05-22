#!/bin/bash
# Historischer Backfill für alle global_XX Spieler >= 2300
# Verwendung: bash scripts/run_hist_backfill.sh HIST_GROUP_NAME
#
# Gruppen und ihre Perioden:
#   hist_2012_h1  →  2012-01-01 bis 2012-05-01
#   hist_2011_h2  →  2011-07-01 bis 2011-11-01
#   hist_2011_h1  →  2011-01-01 bis 2011-05-01
#   hist_2010_h2  →  2010-07-01 bis 2010-11-01
#   hist_2010_h1  →  2010-01-01 bis 2010-05-01

set -e
HIST_GROUP="${1:-}"

if [ -z "$HIST_GROUP" ]; then
    echo "Verwendung: $0 HIST_GROUP_NAME"
    echo "Verfügbare Gruppen: hist_2012_h1 hist_2011_h2 hist_2011_h1 hist_2010_h2 hist_2010_h1"
    exit 1
fi

# Perioden je Gruppe
case "$HIST_GROUP" in
    hist_2012_h1) FROM_DATE="2012-01-01"; TO_DATE="2012-05-01" ;;
    hist_2011_h2) FROM_DATE="2011-07-01"; TO_DATE="2011-11-01" ;;
    hist_2011_h1) FROM_DATE="2011-01-01"; TO_DATE="2011-05-01" ;;
    hist_2010_h2) FROM_DATE="2010-07-01"; TO_DATE="2010-11-01" ;;
    hist_2010_h1) FROM_DATE="2010-01-01"; TO_DATE="2010-05-01" ;;
    *)
        echo "Unbekannte Gruppe: $HIST_GROUP"
        exit 1
        ;;
esac

echo "======================================================"
echo "Historischer Backfill: $HIST_GROUP"
echo "Perioden: $FROM_DATE → $TO_DATE"
echo "Spieler:  alle global_XX mit active=TRUE"
echo "======================================================"

# Alle complete global_XX Gruppen abarbeiten
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Tunnel-Check
python3 -c "
import psycopg2, sys
try:
    conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
    conn.close()
    print('Tunnel OK')
except Exception as e:
    print(f'Tunnel FEHLT: {e}')
    sys.exit(1)
"

# Alle complete global_XX Gruppen holen
GROUPS=$(python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor()
cur.execute(\"SELECT group_name FROM groups WHERE group_name LIKE 'global_%' AND backfill_status='complete' ORDER BY group_name\")
for r in cur.fetchall():
    print(r[0])
conn.close()
")

TOTAL=$(echo "$GROUPS" | wc -l | tr -d ' ')
echo "Verarbeite $TOTAL global_XX Gruppen..."
echo ""

COUNT=0
for GROUP in $GROUPS; do
    COUNT=$((COUNT + 1))
    echo "[$COUNT/$TOTAL] $GROUP ($FROM_DATE → $TO_DATE)"

    # Backfill für diese Gruppe und den historischen Zeitraum
    python3 -m scraper.main run \
        --group "$GROUP" \
        --from "$FROM_DATE" \
        --to "$TO_DATE" \
        2>&1 | grep -E "Saved|ERROR|Warning|Gruppe|done|no_data" | tail -3
done

echo ""
echo "======================================================"
echo "$HIST_GROUP FERTIG"
echo "======================================================"

# Status in groups-Tabelle aktualisieren
python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
cur = conn.cursor()
cur.execute(\"UPDATE groups SET backfill_status='complete' WHERE group_name='$HIST_GROUP'\")
conn.commit()
print('groups-Tabelle aktualisiert: $HIST_GROUP → complete')
conn.close()
"
