#!/usr/bin/env bash
# Monatliches FIDE-Update: Rating-Liste herunterladen, TXT-Snapshot importieren,
# dann P1/P2/P3-Monatsrefresh und P0-Neuzugänge auf dem VPS-Orchestrator anstoßen,
# zuletzt die Datenprüfung (scripts/audit_data.py) als Bericht ablegen.
#
# Läuft komplett ohne Mac Mini / MacBook Pro — das eigentliche Nachscrapen
# übernehmen die dc_update_1/2/3-Threads auf dem VPS (siehe
# orchestrator/generate_monthly_refresh_batches.py / reset_monthly_refresh.py).
#
# TÄGLICH LAUFEN LASSEN, nicht monatlich: FIDE veröffentlicht die neue Liste
# nicht an einem festen Kalendertag. Ist sie noch nicht da, endet das Skript
# sauber mit Exit 0 (No-Op) und versucht es am nächsten Tag erneut. Der Import
# selbst ist idempotent (period_already_imported() in import_rating_snapshots.py),
# ein Doppellauf schadet also nicht.
# Automatisierung: scripts/net.chesspit.fide-monthly-update.plist (launchd, Mac).
#
# Ohne Argument werden die letzten FIDE_LOOKBACK_MONTHS Monate (Default 3)
# geprueft, aelteste zuerst, und alle fehlenden nachgeholt. Damit schliesst sich
# eine Luecke auch dann noch, wenn der Mac ein, zwei Monate aus war — ein Poller,
# der nur den laufenden Monat betrachtet, wuerde sie dauerhaft ueberspringen.
# Der Lookback ist bewusst begrenzt: aeltere Luecken werden NICHT automatisch
# nachimportiert, dafuer den Monat explizit angeben.
#
# Verwendung:
#   bash scripts/monthly_update.sh 2026-05-01    # genau dieser Monat
#   bash scripts/monthly_update.sh               # letzte 3 Monate, fehlende nachholen
#   FIDE_LOOKBACK_MONTHS=6 bash scripts/monthly_update.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB_URL="${DATABASE_URL:-postgresql://fide:nimzo194.@localhost:5434/fidedb}"

# launchd erbt KEIN interaktives Shell-Environment — ein blankes "python3" wäre
# dort entweder das System-Python (ohne psycopg2) oder gar nicht auffindbar.
PY="$SCRIPT_DIR/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

# FIDE-Download: verifiziert am 2026-09-16 — standard_sep26frl.zip liefert
# HTTP 200 (application/zip, 13,3 MB), noch nicht veröffentlichte Monate
# (oct26, jan27) sauber HTTP 404. Das neuere Schema
# standard_rating_list_<mmm><yy>.zip existiert unter /download/ NICHT (404),
# auch wenn import_rating_snapshots.py es beim Einlesen unterstützt.
FIDE_DOWNLOAD_BASE="https://ratings.fide.com/download"

# Doppellauf verhindern (RunAtLoad + StartCalendarInterval können am selben Tag
# beide feuern). Kein flock: macOS liefert nur Bash 3.2 ohne flock(1), siehe
# denselben Hinweis in scripts/pull_backup_macmini.sh.
LOCKDIR="/tmp/fide-monthly-update.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "$(date): Ein anderer Lauf ist noch aktiv ($LOCKDIR) — überspringe."
    exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

echo "$(date): ========================================"
echo "$(date): Monatliches FIDE-Update${1:+ (explizit: $1)}"
echo "$(date): ========================================"

# --- Schritte 3+4 (Definition): VPS-Orchestrator — Refresh-Gruppen requeuen ---
# Schritt 3 setzt NUR die P1/P2/P3-Gruppen zurück (federation-Sentinel, siehe
# orchestrator/monthly_refresh_tiers.py), Schritt 4 NUR die P0-Gruppen (nie
# gescrapte aktive Spieler, ohne Jahres-Rollover — P0 ist bewusst mehrjährig).
# Der laufende Welt-Backfill (dc_ae/de/es/hk/in/mx/uk/us/dach) bleibt in beiden
# Fällen unangetastet; scrape_periods sorgt für idempotentes Überspringen
# bereits gescrapter Perioden.
run_vps_resets() {
    local ok=0
    echo ""
    echo "$(date): === Schritt 3/4: VPS-Orchestrator — P1/P2/P3-Monatsrefresh requeuen ==="
    ssh pit@187.124.181.116 \
        "cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_monthly_refresh.py" \
        || { echo "$(date): WARNUNG: reset_monthly_refresh.py fehlgeschlagen."; ok=1; }

    echo ""
    echo "$(date): === Schritt 4/4: VPS-Orchestrator — P0-Neuzugänge requeuen ==="
    ssh pit@187.124.181.116 \
        "cd /opt/fide-scraper/orchestrator && docker compose exec -T dashboard python3 orchestrator/reset_new_entrant_refresh.py" \
        || { echo "$(date): WARNUNG: reset_new_entrant_refresh.py fehlgeschlagen."; ok=1; }

    return $ok
}

# Offener Reset aus einem früheren Lauf (SSH war z.B. unterwegs nicht erreichbar)?
# Zuerst nachholen — sonst liegt ein importierter Monat da, den niemand nachscrapt.
PENDING_RESET_MARKER="$SCRIPT_DIR/data/.pending_vps_reset"
if [ -f "$PENDING_RESET_MARKER" ]; then
    echo "$(date): Offener VPS-Reset aus früherem Lauf gefunden — hole ihn zuerst nach."
    if run_vps_resets; then
        rm -f "$PENDING_RESET_MARKER"
        echo "$(date): Nachgeholter Reset erfolgreich."
    else
        echo "$(date): Nachholen erneut fehlgeschlagen — Marker bleibt bestehen."
    fi
fi

# Tunnel prüfen
if echo "$DB_URL" | grep -q ":5434"; then
    if ! lsof -i :5434 | grep -q LISTEN 2>/dev/null; then
        echo "$(date): Tunnel nicht aktiv — starte tunnel.sh..."
        bash "$SCRIPT_DIR/scripts/tunnel.sh" &
        sleep 5
    fi
fi

# Ein lauschender Port heißt noch nicht, dass die DB antwortet (halb toter
# Tunnel nach Netzwechsel). Ohne diesen Check liefe der Import in einen
# minutenlangen Timeout statt sofort verständlich abzubrechen.
if ! DB_URL="$DB_URL" "$PY" -c "
import os, sys
import psycopg2
try:
    psycopg2.connect(os.environ['DB_URL'], connect_timeout=10).cursor().execute('SELECT 1')
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
"; then
    echo "$(date): FEHLER: Keine DB-Verbindung über $DB_URL — Tunnel prüfen (scripts/tunnel.sh)."
    exit 1
fi

# --- Zielperioden bestimmen --------------------------------------------------
# Ohne Argument: die letzten LOOKBACK_MONTHS Monate, AELTESTE zuerst. Damit holt
# der Job auch einen Monat nach, der waehrend einer laengeren Mac-Auszeit
# durchgerutscht ist — ein reiner "laufender Monat"-Poller wuerde so eine Luecke
# nie wieder schliessen. Die Reihenfolge alt->neu ist wichtig:
# sync_players_std_rating() aktualisiert players.std_rating nur, wenn die gerade
# importierte Periode die global neueste ist — bei neu->alt bliebe der Sync aus.
LOOKBACK_MONTHS="${FIDE_LOOKBACK_MONTHS:-3}"

if [ -n "${1:-}" ]; then
    PERIODS="$1"
else
    PERIODS=$(LOOKBACK="$LOOKBACK_MONTHS" "$PY" -c "
import os
from datetime import date
n = int(os.environ['LOOKBACK'])
t = date.today()
out, y, m = [], t.year, t.month
for _ in range(n):
    out.append(date(y, m, 1).isoformat())
    m -= 1
    if m == 0:
        y, m = y - 1, 12
print(' '.join(reversed(out)))
")
fi
echo "$(date): Zielperioden (älteste zuerst): $PERIODS"

period_is_imported() {
    DB_URL="$DB_URL" P="$1" SCRIPT_DIR="$SCRIPT_DIR" "$PY" -c "
import os, sys
sys.path.insert(0, os.environ['SCRIPT_DIR'])
import psycopg2
from scripts.import_rating_snapshots import period_already_imported
conn = psycopg2.connect(os.environ['DB_URL'], connect_timeout=10)
sys.exit(0 if period_already_imported(conn, os.environ['P']) else 1)
"
}

find_import_file() {
    P="$1" SCRIPT_DIR="$SCRIPT_DIR" "$PY" - <<'PYEOF'
import sys, os
sys.path.insert(0, os.environ['SCRIPT_DIR'])
from pathlib import Path
from scripts.import_rating_snapshots import period_from_filename

target = os.environ['P']            # z.B. "2026-05-01"
data_dir = Path(os.environ['SCRIPT_DIR']) / 'data'
for f in sorted(data_dir.glob('*.txt')) + sorted(data_dir.glob('*.zip')):
    if period_from_filename(f) == target:
        print(f)
        sys.exit(0)
sys.exit(1)
PYEOF
}

target_filename() {
    P="$1" "$PY" -c "
import os
y, m, _ = os.environ['P'].split('-')
# Feste Monatsliste statt strftime('%b') — das waere locale-abhaengig und
# lieferte unter einer deutschen Locale 'Okt' statt 'oct'.
mmm = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'][int(m)-1]
print(f'standard_{mmm}{y[2:]}frl.zip')
"
}

# Verarbeitet GENAU EINE Periode.
#   0 = importiert (VPS-Requeue noetig)
#   1 = nichts zu tun (schon importiert oder noch nicht veroeffentlicht)
#   2 = Fehler
process_period() {
    local period="$1"
    local import_file target_name target_path url http

    if period_is_imported "$period"; then
        echo "$(date): [$period] bereits importiert — übersprungen."
        return 1
    fi

    import_file=$(find_import_file "$period")

    # Download bewusst NACH der lokalen Suche: liegt die Periode schon unter
    # einem anderen Namensschema vor (z.B. players_list_foa_2026-04.txt), wird
    # nichts geladen.
    if [ -z "$import_file" ]; then
        target_name=$(target_filename "$period")
        target_path="$SCRIPT_DIR/data/$target_name"
        url="$FIDE_DOWNLOAD_BASE/$target_name"

        echo "$(date): [$period] keine lokale Datei — lade $url"
        # Nach .part laden und erst danach umbenennen: eine abgebrochene
        # Uebertragung unter dem Zielnamen wuerde als gueltig erkannt.
        http=$(curl -sS -L --max-time 900 --retry 3 --retry-delay 5 \
                    -o "$target_path.part" -w '%{http_code}' "$url" 2>/dev/null)

        if [ "$http" = "404" ]; then
            rm -f "$target_path.part"
            echo "$(date): [$period] $target_name noch nicht veröffentlicht (HTTP 404)."
            return 1
        fi

        # Echte ZIP? curl schreibt auch Fehlerseiten in die Datei, deshalb
        # Magic-Bytes pruefen statt nur den Statuscode.
        if [ "$http" != "200" ] || [ ! -s "$target_path.part" ] \
           || [ "$(head -c 2 "$target_path.part")" != "PK" ]; then
            rm -f "$target_path.part"
            echo "$(date): [$period] FEHLER: Download von $url fehlgeschlagen (HTTP $http)."
            return 2
        fi

        mv "$target_path.part" "$target_path"
        echo "$(date): [$period] heruntergeladen ($(wc -c < "$target_path" | tr -d ' ') Bytes)"
        import_file=$(find_import_file "$period")
    fi

    if [ -z "$import_file" ]; then
        echo "$(date): [$period] FEHLER: Datei auch nach dem Download nicht auffindbar."
        return 2
    fi

    echo ""
    echo "$(date): === Import $period: $import_file ==="
    if ! DATABASE_URL="$DB_URL" "$PY" "$SCRIPT_DIR/scripts/import_rating_snapshots.py" \
        --file "$import_file"; then
        echo "$(date): [$period] FEHLER: Import fehlgeschlagen."
        return 2
    fi
    return 0
}

# --- Schritte 1+2: je Periode suchen/laden/importieren ------------------------
ANY_IMPORTED=0
HAD_ERROR=0
for PERIOD in $PERIODS; do
    process_period "$PERIOD"
    case $? in
        0) ANY_IMPORTED=1 ;;
        2) HAD_ERROR=1 ;;
    esac
done

# --- Schritte 3+4: nur wenn wirklich etwas Neues dazugekommen ist -------------
if [ "$ANY_IMPORTED" = "0" ]; then
    echo ""
    echo "$(date): Keine neue Periode importiert — VPS-Requeue wird übersprungen."
    [ "$HAD_ERROR" = "1" ] && exit 1
    exit 0
fi

if run_vps_resets; then
    rm -f "$PENDING_RESET_MARKER"
else
    # Ohne Marker bliebe ein gescheiterter Reset unbemerkt liegen: der Import
    # gilt dann als erledigt, aber niemand scrapt den neuen Monat nach.
    touch "$PENDING_RESET_MARKER"
    echo "$(date): Reset vorgemerkt ($PENDING_RESET_MARKER) — der nächste Lauf holt ihn nach."
fi

# --- Schritt 5: Datenprüfung (nur nach echtem Import) ------------------------
# Vollständigkeit + Elo-Plausibilität gegen die offizielle Liste, siehe
# orchestrator/audit.py. Kein --until nötig: audit_data.py deckelt selbst auf
# den Vormonat, die gerade importierte Liste zählt also noch nicht als Lücke.
# Harte Befunde (Exit 1) sind der Normalzustand, solange der Backfill läuft —
# sie dürfen den Monatslauf nicht als fehlgeschlagen markieren.
AUDIT_SINCE="${FIDE_AUDIT_SINCE:-2020-01}"
AUDIT_DIR="$HOME/backups/fide-scraper/audit"
AUDIT_REPORT="$AUDIT_DIR/audit_$(date +%Y-%m-%d).md"
mkdir -p "$AUDIT_DIR"
echo ""
echo "$(date): === Schritt 5: Datenprüfung ab $AUDIT_SINCE ==="
AUDIT_LINE=$(DATABASE_URL="$DB_URL" "$PY" "$SCRIPT_DIR/scripts/audit_data.py" \
    --since "$AUDIT_SINCE" --summary-only --report "$AUDIT_REPORT" \
    2>>"$AUDIT_DIR/audit.log")
if [ -n "$AUDIT_LINE" ]; then
    echo "$(date): $AUDIT_LINE"
    echo "$(date): Bericht: $AUDIT_REPORT"
else
    echo "$(date): WARNUNG: Datenprüfung abgebrochen — siehe $AUDIT_DIR/audit.log"
fi

echo ""
echo "$(date): ========================================"
echo "$(date): Monatliches Update abgeschlossen ✓"
echo "$(date): dc_update_1..3 holen den neuen Monat im Hintergrund nach (VPS-Dashboard),"
echo "$(date): dc_newplayers_1/2 die seit letztem Monat neu aktiven, nie gescrapten Spieler."
echo "$(date): ========================================"
[ "$HAD_ERROR" = "1" ] && exit 1
exit 0
