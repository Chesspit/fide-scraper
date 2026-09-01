#!/usr/bin/env bash
# Monatliches FIDE-Update: TXT-Snapshot importieren, dann P1/P2/P3-Monatsrefresh
# auf dem VPS-Orchestrator anstoßen.
#
# Läuft komplett ohne Mac Mini / MacBook Pro — das eigentliche Nachscrapen
# übernehmen die dc_update_1/2/3-Threads auf dem VPS (siehe
# orchestrator/generate_monthly_refresh_batches.py / reset_monthly_refresh.py).
#
# Voraussetzung: TXT-Datei bereits in data/ abgelegt
#   data/players_list_foa_YYYY-MM.txt  (oder .zip)  bzw. standard_*frl.zip
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

echo "$(date): ========================================"
echo "$(date): Monatliches FIDE-Update: $NEW_PERIOD"
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
echo "$(date): === Schritt 2/3: TXT-Snapshot importieren ==="
DATABASE_URL="$DB_URL" python3 "$SCRIPT_DIR/scripts/import_rating_snapshots.py" \
    --file "$IMPORT_FILE"

# --- Schritt 3: VPS-Orchestrator — P1/P2/P3-Monatsrefresh requeuen ---
# Setzt NUR die P1/P2/P3-Gruppen (federation-Sentinel, siehe
# orchestrator/monthly_refresh_tiers.py) zurück — der separate, laufende
# Welt-Backfill (dc_ae/de/es/hk/in/mx/uk/us/dach) bleibt unangetastet.
# PostgreSQL scrape_periods sorgt für idempotentes Überspringen bereits
# gescrapter Perioden — nur der neue Monat wird tatsächlich nachgeholt.
echo ""
echo "$(date): === Schritt 3/4: VPS-Orchestrator — P1/P2/P3-Monatsrefresh requeuen ==="
if ! ssh pit@187.124.181.116 \
    "cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_monthly_refresh.py"; then
    echo "$(date): WARNUNG: reset_monthly_refresh.py auf VPS fehlgeschlagen — manuell nachholen:"
    echo "  ssh pit@187.124.181.116 \"cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_monthly_refresh.py\""
fi

# --- Schritt 4: VPS-Orchestrator — P0-Neuzugänge requeuen ---
# Setzt NUR die P0-Gruppen zurück (nie gescrapte, aktive Spieler seit dem
# letzten Lauf, siehe orchestrator/reset_new_entrant_refresh.py) — anders
# als P1/P2/P3 KEIN Jahres-Rollover (P0 ist bewusst mehrjährig, 2025+2026).
echo ""
echo "$(date): === Schritt 4/4: VPS-Orchestrator — P0-Neuzugänge requeuen ==="
if ! ssh pit@187.124.181.116 \
    "cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_new_entrant_refresh.py"; then
    echo "$(date): WARNUNG: reset_new_entrant_refresh.py auf VPS fehlgeschlagen — manuell nachholen:"
    echo "  ssh pit@187.124.181.116 \"cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_new_entrant_refresh.py\""
fi

echo ""
echo "$(date): ========================================"
echo "$(date): Monatliches Update $NEW_PERIOD abgeschlossen ✓"
echo "$(date): dc_update_1..3 holen den neuen Monat im Hintergrund nach (VPS-Dashboard),"
echo "$(date): dc_newplayers_1/2 die seit letztem Monat neu aktiven, nie gescrapten Spieler."
echo "$(date): ========================================"
