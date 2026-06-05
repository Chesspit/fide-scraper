#!/usr/bin/env bash
# Monatliches Update-Script für benannte Spieler-Gruppen (UP-*).
# Liest Job-Definition aus update_jobs.yaml, fragt relevante Spieler-IDs
# aus der DB ab und startet Backfill für den konfigurierten Zeitraum.
#
# Verwendung:
#   bash scripts/run_update_job.sh UP-GER
#   bash scripts/run_update_job.sh UP-GER 2026-01-01 2026-05-01
#
# VPS (docker):
#   docker compose run -T worker bash scripts/run_update_job.sh UP-GER

set -uo pipefail

JOB=${1:?Job-Name angeben (z.B. UP-GER). Verfügbar: UP-ELO2300 UP-FEMALE UP-GER UP-DACH}
DB_URL="${DATABASE_URL:-postgresql://fide:nimzo194.@localhost:5434/fidedb}"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="/tmp/update_job_${JOB}.log"
IDS_FILE="/tmp/update_job_${JOB}_ids.txt"

# FROM: Jahresanfang des laufenden Jahres (fängt ggf. fehlende Monate seit Jan auf)
FROM=${2:-$(python3 -c "from datetime import date; print(date(date.today().year, 1, 1))")}
# TO: letzter abgeschlossener FIDE-Monat
TO=${3:-$(python3 -c "from datetime import date; t=date.today(); m=t.month-1 or 12; y=t.year if t.month>1 else t.year-1; print(date(y,m,1))")}

echo "$(date): Update-Job $JOB — FROM=$FROM TO=$TO"
echo "Log: $LOG"

# Tunnel prüfen (nur nötig wenn lokaler Port 5434 verwendet wird)
if echo "$DB_URL" | grep -q ":5434"; then
    if ! lsof -i :5434 | grep -q LISTEN 2>/dev/null; then
        echo "$(date): Tunnel nicht aktiv — starte tunnel.sh..."
        bash "$SCRIPT_DIR/scripts/tunnel.sh" &
        sleep 5
    fi
fi

# Spieler-IDs aus update_jobs.yaml ermitteln und in Temp-Datei schreiben
JOB="$JOB" DB_URL="$DB_URL" IDS_FILE="$IDS_FILE" YAML_PATH="$SCRIPT_DIR/update_jobs.yaml" \
python3 - <<'PYEOF'
import sys, os, yaml, psycopg2

job_name = os.environ['JOB']
db_url   = os.environ['DB_URL']
ids_file = os.environ['IDS_FILE']
yaml_path = os.environ['YAML_PATH']

with open(yaml_path) as f:
    jobs = yaml.safe_load(f)['jobs']

if job_name not in jobs:
    print(f'FEHLER: Unbekannter Job "{job_name}"', file=sys.stderr)
    print('Verfügbare Jobs: ' + ', '.join(jobs.keys()), file=sys.stderr)
    sys.exit(1)

job = jobs[job_name]
print(f'Job: {job_name} — {job["description"]}')

conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute(f"""
    SELECT DISTINCT sp.fide_id
    FROM scrape_periods sp
    JOIN players p ON p.fide_id = sp.fide_id
    WHERE sp.status = 'ok'
      AND ({job['filter']})
    ORDER BY sp.fide_id
""")
ids = [str(r[0]) for r in cur.fetchall()]
conn.close()

with open(ids_file, 'w') as f:
    f.write('\n'.join(ids))
print(f'{len(ids)} Spieler-IDs → {ids_file}')
PYEOF

IDS_COUNT=$(wc -l < "$IDS_FILE" | tr -d ' ')
echo "$(date): $IDS_COUNT Spieler gefunden."

if [ "$IDS_COUNT" -eq 0 ]; then
    echo "$(date): Keine Spieler für Job $JOB — abgebrochen."
    exit 0
fi

# Backfill starten (caffeinate nur auf macOS, Auto-Restart bei Absturz)
echo "$(date): caffeinate aktiv (falls macOS) — starte Backfill..."
while true; do
    if command -v caffeinate &>/dev/null; then
        caffeinate -i env DATABASE_URL="$DB_URL" \
            python3 "$SCRIPT_DIR/scripts/backfill.py" \
            --from "$FROM" --to "$TO" \
            --fide-ids-file "$IDS_FILE" \
            --reverse \
            >> "$LOG" 2>&1
    else
        env DATABASE_URL="$DB_URL" \
            python3 "$SCRIPT_DIR/scripts/backfill.py" \
            --from "$FROM" --to "$TO" \
            --fide-ids-file "$IDS_FILE" \
            --reverse \
            >> "$LOG" 2>&1
    fi

    EXIT=$?

    if tail -1 "$LOG" 2>/dev/null | grep -q "Backfill complete"; then
        echo "$(date): $JOB abgeschlossen ✓"
        rm -f "$IDS_FILE"
        break
    fi

    if [ $EXIT -eq 0 ]; then
        echo "$(date): $JOB exit 0 ohne 'Backfill complete' — fertig."
        rm -f "$IDS_FILE"
        break
    fi

    echo "$(date): Absturz (exit $EXIT) — Neustart in 15s..."
    sleep 15

    if echo "$DB_URL" | grep -q ":5434"; then
        if ! lsof -i :5434 | grep -q LISTEN 2>/dev/null; then
            echo "$(date): Tunnel weg — starte neu..."
            bash "$SCRIPT_DIR/scripts/tunnel.sh" &
            sleep 10
        fi
    fi
done

echo "$(date): run_update_job.sh $JOB beendet."
