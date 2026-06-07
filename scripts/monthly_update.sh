#!/usr/bin/env bash
# Monatliches FIDE-Update: TXT-Snapshot importieren und alle UP-Jobs ausführen.
#
# Voraussetzung: TXT-Datei bereits in data/ abgelegt
#   data/players_list_foa_YYYY-MM.txt  (oder .zip)
#
# Verwendung:
#   bash scripts/monthly_update.sh 2026-05-01    # expliziter Monat
#   bash scripts/monthly_update.sh               # auto: letzter Monat

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB_URL="${DATABASE_URL:-postgresql://fide:nimzo194.@localhost:5434/fidedb}"

# Ziel-Monat ermitteln
NEW_PERIOD=${1:-$(python3 -c "
from datetime import date
t = date.today()
m = t.month - 1 or 12
y = t.year if t.month > 1 else t.year - 1
print(date(y, m, 1))
")}

YEAR_MONTH="${NEW_PERIOD:0:7}"          # z.B. "2026-05"
FROM_DATE="${NEW_PERIOD:0:4}-01-01"     # z.B. "2026-01-01" — fängt Lücken seit Jan auf

echo "$(date): ========================================"
echo "$(date): Monatliches FIDE-Update: $NEW_PERIOD"
echo "$(date): FROM=$FROM_DATE TO=$NEW_PERIOD"
echo "$(date): ========================================"

# --- Schritt 1: TXT-Datei suchen (unterstützt alle FIDE-Namensformate) ---
IMPORT_FILE=$(NEW_PERIOD="$NEW_PERIOD" SCRIPT_DIR="$SCRIPT_DIR" python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, os.environ['SCRIPT_DIR'])
from pathlib import Path
from scripts.import_rating_snapshots import period_from_filename

target = os.environ['NEW_PERIOD']   # z.B. "2026-05-01"
data_dir = Path(os.environ['SCRIPT_DIR']) / 'data'
for f in sorted(data_dir.glob('*.txt')) + sorted(data_dir.glob('*.zip')):
    if period_from_filename(f) == target:
        print(f)
        sys.exit(0)
sys.exit(1)
PYEOF
)

if [ -z "$IMPORT_FILE" ]; then
    echo ""
    echo "FEHLER: Keine TXT/ZIP-Datei für Monat $NEW_PERIOD in data/ gefunden."
    echo "Herunterladen von: https://ratings.fide.com/download_lists.phtml"
    exit 1
fi
echo "$(date): TXT-Datei: $IMPORT_FILE"

# Tunnel prüfen
if echo "$DB_URL" | grep -q ":5434"; then
    if ! lsof -i :5434 | grep -q LISTEN 2>/dev/null; then
        echo "$(date): Tunnel nicht aktiv — starte tunnel.sh..."
        bash "$SCRIPT_DIR/scripts/tunnel.sh" &
        sleep 5
    fi
fi

# --- Schritt 2: TXT-Snapshot importieren ---
echo ""
echo "$(date): === Schritt 2/4: TXT-Snapshot importieren ==="
DATABASE_URL="$DB_URL" python3 "$SCRIPT_DIR/scripts/import_rating_snapshots.py" \
    --file "$IMPORT_FILE"

# --- Schritt 3: config.yaml aktualisieren ---
echo ""
echo "$(date): === Schritt 3/4: config.yaml aktualisieren ==="
YEAR_MONTH="$YEAR_MONTH" CONFIG="$SCRIPT_DIR/config.yaml" python3 - <<'PYEOF'
import re, os
config_path = os.environ['CONFIG']
year_month  = os.environ['YEAR_MONTH']
with open(config_path) as f:
    content = f.read()
new_ref = f'data/players_list_foa_{year_month}.txt'
content = re.sub(r'players_file:.*', f'players_file: {new_ref}', content)
with open(config_path, 'w') as f:
    f.write(content)
print(f'config.yaml → players_file: {new_ref}')
PYEOF

# --- Schritt 4: UP-Jobs ausführen ---
echo ""
echo "$(date): === Schritt 4/5: Update-Jobs starten ==="
JOBS=(UP-ELO2300 UP-FEMALE UP-GER UP-DACH)

for JOB in "${JOBS[@]}"; do
    echo ""
    echo "$(date): --- $JOB ---"
    bash "$SCRIPT_DIR/scripts/run_update_job.sh" "$JOB" "$FROM_DATE" "$NEW_PERIOD"
done

# --- Schritt 5: VPS-Orchestrator — done-Gruppen des laufenden Jahres requeuen ---
# Betrifft auch die dc_update-Batches (Rest-Population): PostgreSQL scrape_periods
# sorgt für idempotentes Überspringen bereits gescrapter Perioden — nur der neue
# Monat wird tatsächlich nachgeholt.
echo ""
echo "$(date): === Schritt 5/5: VPS-Orchestrator — Update-Batches requeuen ==="
if ! ssh pit@187.124.181.116 \
    "cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_current_year.py"; then
    echo "$(date): WARNUNG: reset_current_year.py auf VPS fehlgeschlagen — manuell nachholen:"
    echo "  ssh pit@187.124.181.116 \"cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_current_year.py\""
fi

echo ""
echo "$(date): ========================================"
echo "$(date): Monatliches Update $NEW_PERIOD abgeschlossen ✓"
echo "$(date): ========================================"
