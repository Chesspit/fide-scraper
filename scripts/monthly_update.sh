#!/usr/bin/env bash
# Monatliches FIDE-Update: Rating-Liste herunterladen, TXT-Snapshot importieren,
# dann P1/P2/P3-Monatsrefresh und P0-Neuzugänge auf dem VPS-Orchestrator anstoßen.
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
# Verwendung:
#   bash scripts/monthly_update.sh 2026-05-01    # expliziter Monat
#   bash scripts/monthly_update.sh               # auto: letzter Monat

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

# Ziel-Monat ermitteln: der LAUFENDE Monat, nicht der Vormonat.
# FIDE benennt die Liste nach dem Monat ihrer Veröffentlichung (standard_sep26frl.zip
# = Periode 2026-09-01, Inhalt sind die Partien des Vormonats). Ein Tages-Poller, der
# auf den Vormonat zielt, wuerde die neue Liste erst einen Monat zu spaet anfassen.
NEW_PERIOD=${1:-$("$PY" -c "
from datetime import date
t = date.today()
print(date(t.year, t.month, 1))
")}

echo "$(date): ========================================"
echo "$(date): Monatliches FIDE-Update: $NEW_PERIOD"
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

# --- Abbruch, wenn die Periode längst drin ist -------------------------------
# ENTSCHEIDEND fuer den taeglichen Lauf: ohne diesen Check wuerde das Skript
# jeden Tag die VPS-Resets (Schritte 3+4) ausloesen und damit P1/P2/P3 und P0
# taeglich neu requeuen — permanente Churn auf einer Queue, die eigentlich nur
# einmal pro Monat angefasst werden soll. Mit dem Check ist ein Lauf an einem
# Tag ohne neue Liste ein echtes No-Op (kein Download, kein Import, kein Reset).
if DB_URL="$DB_URL" NEW_PERIOD="$NEW_PERIOD" SCRIPT_DIR="$SCRIPT_DIR" "$PY" -c "
import os, sys
sys.path.insert(0, os.environ['SCRIPT_DIR'])
import psycopg2
from scripts.import_rating_snapshots import period_already_imported
conn = psycopg2.connect(os.environ['DB_URL'], connect_timeout=10)
sys.exit(0 if period_already_imported(conn, os.environ['NEW_PERIOD']) else 1)
"; then
    echo "$(date): Periode $NEW_PERIOD ist bereits importiert — nichts zu tun."
    echo "$(date): (Erneuter Import und VPS-Requeue werden bewusst übersprungen.)"
    exit 0
fi

# data/ liegt nicht im Repo (nur .gitignore-Einträge) und fehlt auf frischen
# Checkouts komplett — ohne mkdir scheitert die Suche unten am fehlenden Ordner.
mkdir -p "$SCRIPT_DIR/data"

find_import_file() {
    NEW_PERIOD="$NEW_PERIOD" SCRIPT_DIR="$SCRIPT_DIR" "$PY" - <<'PYEOF'
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
}

# --- Schritt 1: TXT-Datei suchen (unterstützt alle FIDE-Namensformate) ---
IMPORT_FILE=$(find_import_file)

# --- Schritt 1b: fehlt sie, von FIDE laden ---
# Bewusst NACH der Suche: liegt die Periode bereits unter einem anderen
# Namensschema vor (z.B. players_list_foa_2026-04.txt), wird nichts geladen.
if [ -z "$IMPORT_FILE" ]; then
    TARGET_NAME=$(NEW_PERIOD="$NEW_PERIOD" "$PY" -c "
import os
y, m, _ = os.environ['NEW_PERIOD'].split('-')
# Feste Monatsliste statt strftime('%b') — das waere locale-abhaengig und
# lieferte unter einer deutschen Locale 'Okt' statt 'oct'.
mmm = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'][int(m)-1]
print(f'standard_{mmm}{y[2:]}frl.zip')
")
    TARGET_PATH="$SCRIPT_DIR/data/$TARGET_NAME"
    URL="$FIDE_DOWNLOAD_BASE/$TARGET_NAME"

    echo "$(date): Keine lokale Datei für $NEW_PERIOD — lade $URL"
    # Nach .part laden und erst danach umbenennen: ein abgebrochener Download
    # unter dem Zielnamen wuerde von find_import_file() als gueltig erkannt.
    HTTP=$(curl -sS -L --max-time 900 --retry 3 --retry-delay 5 \
                -o "$TARGET_PATH.part" -w '%{http_code}' "$URL" 2>/dev/null)

    if [ "$HTTP" = "404" ]; then
        rm -f "$TARGET_PATH.part"
        echo "$(date): FIDE-Liste $TARGET_NAME noch nicht veröffentlicht (HTTP 404) — nichts zu tun."
        echo "$(date): (Kein Fehler: das Skript läuft täglich und holt sie, sobald sie da ist.)"
        exit 0
    fi

    # Echte ZIP? curl schreibt auch Fehlerseiten in die Datei, deshalb Magic-Bytes
    # pruefen statt nur den Statuscode.
    if [ "$HTTP" != "200" ] || [ ! -s "$TARGET_PATH.part" ] \
       || [ "$(head -c 2 "$TARGET_PATH.part")" != "PK" ]; then
        rm -f "$TARGET_PATH.part"
        echo "$(date): FEHLER: Download von $URL fehlgeschlagen (HTTP $HTTP)."
        exit 1
    fi

    mv "$TARGET_PATH.part" "$TARGET_PATH"
    echo "$(date): Heruntergeladen: $TARGET_PATH ($(wc -c < "$TARGET_PATH" | tr -d ' ') Bytes)"
    IMPORT_FILE=$(find_import_file)
fi

if [ -z "$IMPORT_FILE" ]; then
    echo "$(date): FEHLER: Datei für $NEW_PERIOD auch nach dem Download nicht auffindbar."
    exit 1
fi
echo "$(date): TXT-Datei: $IMPORT_FILE"

# --- Schritt 2: TXT-Snapshot importieren ---
echo ""
echo "$(date): === Schritt 2/4: TXT-Snapshot importieren ==="
if ! DATABASE_URL="$DB_URL" "$PY" "$SCRIPT_DIR/scripts/import_rating_snapshots.py" \
    --file "$IMPORT_FILE"; then
    echo "$(date): FEHLER: Import von $IMPORT_FILE fehlgeschlagen — VPS-Schritte werden übersprungen."
    exit 1
fi

if run_vps_resets; then
    rm -f "$PENDING_RESET_MARKER"
else
    # Ohne Marker bliebe ein gescheiterter Reset unbemerkt liegen: der Import
    # gilt dann als erledigt, aber niemand scrapt den neuen Monat nach.
    touch "$PENDING_RESET_MARKER"
    echo "$(date): Reset vorgemerkt ($PENDING_RESET_MARKER) — der nächste Lauf holt ihn nach."
fi

echo ""
echo "$(date): ========================================"
echo "$(date): Monatliches Update $NEW_PERIOD abgeschlossen ✓"
echo "$(date): dc_update_1..3 holen den neuen Monat im Hintergrund nach (VPS-Dashboard),"
echo "$(date): dc_newplayers_1/2 die seit letztem Monat neu aktiven, nie gescrapten Spieler."
echo "$(date): ========================================"
