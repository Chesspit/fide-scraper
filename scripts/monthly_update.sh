#!/usr/bin/env bash
# Monatliches FIDE-Update: Rating-Liste herunterladen, TXT-Snapshot importieren,
# dann Listen-Gruppen für die neue Periode (stark → DACH → Rest) auf dem
# VPS-Orchestrator anlegen, zuletzt die Datenprüfung (scripts/audit_data.py)
# als Bericht ablegen.
#
# ANHALTEN: Liegt auf dem VPS die Datei $FIDE_HOLD_FILE (Default
# ~/HOLD_FIDE_MONTHLY_UPDATE), endet jeder Lauf sofort ohne Download, Import oder
# Requeue — auch ein vorgemerkter Reset wartet. Setzen/aufheben von jedem Rechner:
#   ssh pit@<vps> 'echo "Grund" > ~/HOLD_FIDE_MONTHLY_UPDATE'
#   ssh pit@<vps> 'rm ~/HOLD_FIDE_MONTHLY_UPDATE'
# Ist der VPS nicht erreichbar, wird der Lauf ebenfalls übersprungen (die
# VPS-Schritte gingen ohnehin nicht) und am nächsten Tag wiederholt.
#
# Das eigentliche Nachscrapen übernehmen die Threads auf dem VPS
# (orchestrator/generate_list_batches.py --period, siehe Schritt 4).
#
# Der launchd-Job läuft TÄGLICH, weil die Anreicherung neu gescrapter Partien
# (resolve_opponents --pending) jeden Tag Arbeit hat. Die LISTE wird aber nur an
# den Tagen FIDE_LIST_CHECK_DAYS geprüft (Default "2 14 28", seit 14.11.2026):
#   - neue Liste da? → laden + importieren + VPS-Requeue + Datenprüfung
#   - neueste importierte Liste bei FIDE verändert? → neu laden und mit der
#     lokalen Kopie vergleichen. Bei Abweichung Warnung + Kopie unter
#     data/revisions/. An den Korrekturtagen FIDE_REVISION_APPLY_DAYS (Default
#     "14 28") direkt danach scripts/apply_list_revision.py: Liste neu
#     einspielen, nur Kombos mit geänderter Partienzahl/Neuzugänge neu scrapen
#     (GAP-Gruppe). Gesperrt, solange P1–P3- oder Gruppen dieser Periode offen
#     sind — dann verschoben auf den nächsten Korrekturtag. Am 2. nur Warnung.
#     Sofort korrigieren: FIDE_FORCE_LIST_CHECK=1 FIDE_FORCE_REVISION_APPLY=1
# An allen anderen Tagen wird die Liste übersprungen. Erzwingen:
#   FIDE_FORCE_LIST_CHECK=1 bash scripts/monthly_update.sh
# Ein explizit angegebener Monat ($1) prüft immer. Der Import selbst ist
# idempotent (period_already_imported() in import_rating_snapshots.py).
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
. "$(cd "$(dirname "$0")" && pwd)/vps.env"   # FIDE_VPS, FIDE_VPS_COMPOSE_DIR
# DB-Zugang aus .env (das Passwort steht bewusst nicht im Repo)
DATABASE_URL="${DATABASE_URL:-$(grep -m1 '^DATABASE_URL=' "$(cd "$(dirname "$0")/.." && pwd)/.env" 2>/dev/null | cut -d= -f2-)}"
: "${DATABASE_URL:?DATABASE_URL fehlt — in .env oder Umgebung setzen}"

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB_URL="$DATABASE_URL"

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

# Zentrale Sperre auf dem VPS (siehe Kopf). Liegt dort statt lokal, damit sie
# auch den launchd-Lauf auf einem anderen Mac anhält.
FIDE_HOLD_FILE="${FIDE_HOLD_FILE:-~/HOLD_FIDE_MONTHLY_UPDATE}"
HOLD_REASON=$(ssh -o ConnectTimeout=15 -o BatchMode=yes "$FIDE_VPS" \
    "if [ -e $FIDE_HOLD_FILE ]; then echo HOLD; cat $FIDE_HOLD_FILE; else echo FREE; fi" 2>/dev/null)
case "$HOLD_REASON" in
    FREE) ;;
    HOLD*)
        echo "$(date): ANGEHALTEN per $FIDE_VPS:$FIDE_HOLD_FILE — kein Download, Import oder Requeue."
        echo "$(date): Grund: $(echo "$HOLD_REASON" | sed -n 2p)"
        exit 0 ;;
    *)
        echo "$(date): VPS nicht erreichbar, Sperre nicht prüfbar — Lauf übersprungen, morgen erneut."
        exit 0 ;;
esac

# --- Schritt 4 (Definition): VPS-Orchestrator — Listen-Gruppen anlegen ------
# Für jede neu importierte Periode legt generate_list_batches.py --period die
# Soll-Kombos (Partien laut Liste, noch ohne scrape_periods-Zeile) in drei
# Stufen an, Priorität in dieser Reihenfolge: GAP1 stark (Elo ≥ 2300) → GAP2
# DACH → GAP3 Rest (orchestrator/monthly_refresh_tiers.LIST_PERIOD_TIERS).
# Stufen 1+2 laufen auf dc_update_1, der Rest verteilt auf die Backfill-Threads.
# Erfasst Neuzugänge, Reaktivierte und Inaktive (kein active-Filter).
# Seit 14.11.2026 OHNE den früheren P1/P2/P3-Requeue (reset_monthly_refresh.py):
# der prüfte das ganze Jahr aller schon gescrapten Spieler und fand im November
# 2026 nur noch 8.606 Partien in 105 h, weil die Listen-Gruppen dieselben
# Kombos zuerst holen. Läuft im worker-Container (nur dessen Image kennt die
# Listen-Gruppen). Bereits gescrapte Kombos überspringt der Worker idempotent.
# $1 = Leerzeichen-getrennte Perioden (YYYY-MM-01).
run_vps_resets() {
    local ok=0 period
    for period in $1; do
        echo ""
        echo "$(date): === Schritt 4: VPS-Orchestrator — Listen-Gruppen für $period ==="
        ssh "$FIDE_VPS" \
            "cd $FIDE_VPS_COMPOSE_DIR && docker compose exec -T worker python3 orchestrator/generate_list_batches.py --period $period" \
            || { echo "$(date): WARNUNG: generate_list_batches.py --period $period fehlgeschlagen."; ok=1; }
    done

    return $ok
}

# GAP-Gruppe für eine korrigierte Liste (nur die neu zu scrapenden Kombos).
# Scheitert SSH, merkt sich ein Marker die Periode für den nächsten Lauf —
# ohne Gruppe blieben die gelöschten Kombos ungescrapt.
PENDING_REVISION_MARKER="$SCRIPT_DIR/data/.pending_revision_gap"
run_revision_gap() {
    echo "$(date): [$1] GAP-Gruppe für die korrigierten Kombos anlegen"
    if ssh "$FIDE_VPS" \
        "cd $FIDE_VPS_COMPOSE_DIR && docker compose exec -T worker python3 orchestrator/generate_list_batches.py --period $1"; then
        rm -f "$PENDING_REVISION_MARKER"
    else
        echo "$1" > "$PENDING_REVISION_MARKER"
        echo "$(date): [$1] WARNUNG: GAP-Gruppe nicht angelegt — vorgemerkt ($PENDING_REVISION_MARKER)."
    fi
}
if [ -f "$PENDING_REVISION_MARKER" ]; then
    echo "$(date): Offene GAP-Gruppe einer Listen-Korrektur gefunden — hole sie nach."
    run_revision_gap "$(cat "$PENDING_REVISION_MARKER")"
fi

# Offener Reset aus einem früheren Lauf (SSH war z.B. unterwegs nicht erreichbar)?
# Zuerst nachholen — sonst liegt ein importierter Monat da, den niemand nachscrapt.
PENDING_RESET_MARKER="$SCRIPT_DIR/data/.pending_vps_reset"
if [ -f "$PENDING_RESET_MARKER" ]; then
    echo "$(date): Offener VPS-Reset aus früherem Lauf gefunden — hole ihn zuerst nach."
    # Marker enthält die Perioden (leer bei Markern aus der Zeit vor 30.10.2026)
    if run_vps_resets "$(cat "$PENDING_RESET_MARKER")"; then
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

# Prüft, ob FIDE die Liste einer schon importierten Periode verändert hat:
# neu laden, den TXT-Inhalt mit der lokalen Datei vergleichen (ZIP-Bytes allein
# taugen nicht, Zeitstempel im Archiv können sich ohne Inhaltsänderung ändern).
# Am Korrekturtag danach apply_list_revision.py (s. Kopf).
check_list_revision() {
    local period="$1" local_file target_name tmpdir url http local_txt new_txt changed revdir revfile rc d apply
    local_file=$(find_import_file "$period")
    if [ -z "$local_file" ]; then
        echo "$(date): [$period] Änderungsprüfung: keine lokale Kopie in data/ — übersprungen."
        return 0
    fi
    target_name=$(target_filename "$period")
    url="$FIDE_DOWNLOAD_BASE/$target_name"
    tmpdir=$(mktemp -d)
    http=$(curl -sS -L --max-time 900 --retry 3 --retry-delay 5 \
                -o "$tmpdir/$target_name" -w '%{http_code}' "$url" 2>/dev/null)
    if [ "$http" != "200" ] || [ "$(head -c 2 "$tmpdir/$target_name" 2>/dev/null)" != "PK" ]; then
        echo "$(date): [$period] Änderungsprüfung: Download fehlgeschlagen (HTTP $http) — übersprungen."
        rm -rf "$tmpdir"
        return 0
    fi
    local_txt="$tmpdir/local.txt"
    new_txt="$tmpdir/new.txt"
    case "$local_file" in
        *.zip) unzip -p "$local_file" > "$local_txt" ;;
        *)     cp "$local_file" "$local_txt" ;;
    esac
    unzip -p "$tmpdir/$target_name" > "$new_txt"
    if cmp -s "$local_txt" "$new_txt"; then
        echo "$(date): [$period] Änderungsprüfung: Liste bei FIDE unverändert."
    else
        changed=$(diff "$local_txt" "$new_txt" | grep -c '^>')
        revdir="$SCRIPT_DIR/data/revisions"
        mkdir -p "$revdir"
        # Unterordner, damit find_import_file (nur data/*.zip) die Revision nicht
        # versehentlich als Importdatei nimmt.
        revfile="$revdir/${target_name%.zip}_$(date +%Y-%m-%d).zip"
        cp "$tmpdir/$target_name" "$revfile"
        echo "$(date): [$period] ⚠️  WARNUNG: Liste bei FIDE VERÄNDERT — $changed neue/geänderte Zeilen."
        echo "$(date): [$period] Neue Fassung: $revfile"

        apply=0
        [ "${FIDE_FORCE_REVISION_APPLY:-0}" = "1" ] && apply=1
        for d in ${FIDE_REVISION_APPLY_DAYS:-14 28}; do [ "$d" = "$TODAY_DAY" ] && apply=1; done
        if [ "$apply" = "0" ]; then
            echo "$(date): [$period] Heute kein Korrekturtag (${FIDE_REVISION_APPLY_DAYS:-14 28}) — nur gemeldet."
        else
            echo "$(date): [$period] === Korrektur der Liste (apply_list_revision.py) ==="
            DATABASE_URL="$DB_URL" "$PY" "$SCRIPT_DIR/scripts/apply_list_revision.py" \
                --period "$period" --old "$local_file" --new "$revfile" \
                | sed "s/^/$(date): [$period]   /"
            rc=${PIPESTATUS[0]}
            case $rc in
                0) run_revision_gap "$period" ;;
                3) echo "$(date): [$period] Korrektur verschoben — Monatsprozesse noch nicht abgeschlossen." ;;
                4) ;;
                *) echo "$(date): [$period] FEHLER: Korrektur fehlgeschlagen (Exit $rc)."; HAD_ERROR=1 ;;
            esac
        fi
    fi
    rm -rf "$tmpdir"
    return 0
}

# --- Schritte 1+2: nur an Prüftagen je Periode suchen/laden/importieren -------
ANY_IMPORTED=0
IMPORTED_PERIODS=""
HAD_ERROR=0
LIST_CHECK_DAYS="${FIDE_LIST_CHECK_DAYS:-2 14 28}"
TODAY_DAY=$(date +%-d)
LIST_CHECK=0
[ -n "${1:-}" ] && LIST_CHECK=1
[ "${FIDE_FORCE_LIST_CHECK:-0}" = "1" ] && LIST_CHECK=1
for d in $LIST_CHECK_DAYS; do [ "$d" = "$TODAY_DAY" ] && LIST_CHECK=1; done

if [ "$LIST_CHECK" = "0" ]; then
    echo "$(date): Heute kein Listen-Prüftag (Prüftage: $LIST_CHECK_DAYS) — Liste übersprungen."
else
    LATEST_OLD=""   # neueste Periode, die schon VOR diesem Lauf importiert war
    for PERIOD in $PERIODS; do
        process_period "$PERIOD"
        case $? in
            0) ANY_IMPORTED=1; IMPORTED_PERIODS="$IMPORTED_PERIODS $PERIOD" ;;
            1) period_is_imported "$PERIOD" && LATEST_OLD="$PERIOD" ;;
            2) HAD_ERROR=1 ;;
        esac
    done
    # Nur die zuletzt veröffentlichte Liste prüfen; eine gerade frisch geladene
    # ist per Definition aktuell.
    if [ -n "$LATEST_OLD" ] && [ -z "$IMPORTED_PERIODS" ]; then
        check_list_revision "$LATEST_OLD"
    fi
fi

# --- Täglich: neu gescrapte Partien anreichern -------------------------------
# Gegner-FIDE-ID, Geschlecht, Turniertyp, Erwartungswert (scripts/resolve_opponents.py
# --pending). Läuft jeden Tag, unabhängig vom Import: die VPS-Threads scrapen
# laufend nach. Nur Partien ohne opponent_match_quality (Teilindex, Migration 027);
# ohne offene Partien ein No-Op. Fehler brechen den Monatslauf nicht ab.
# Abschalten: FIDE_RESOLVE_PENDING=0.
if [ "${FIDE_RESOLVE_PENDING:-1}" = "1" ]; then
    RESOLVE_DIR="$HOME/backups/fide-scraper/resolve"
    mkdir -p "$RESOLVE_DIR"
    RESOLVE_ARGS=(--pending --fuzzy --pause 1)
    RATINGS_PARQUET="$SCRIPT_DIR/frontend/player_history_cache.parquet"
    [ -f "$RATINGS_PARQUET" ] && RESOLVE_ARGS+=(--ratings-parquet "$RATINGS_PARQUET")
    echo ""
    echo "$(date): === Anreicherung neuer Partien (resolve_opponents --pending) ==="
    if DATABASE_URL="$DB_URL" "$PY" "$SCRIPT_DIR/scripts/resolve_opponents.py" \
        "${RESOLVE_ARGS[@]}" >>"$RESOLVE_DIR/pending.log" 2>&1; then
        echo "$(date): $(tail -n 8 "$RESOLVE_DIR/pending.log" | grep -E 'Fertig|Keine Perioden|zugeordnet' | tr '\n' ' ')"
    else
        echo "$(date): WARNUNG: Anreicherung fehlgeschlagen — siehe $RESOLVE_DIR/pending.log"
    fi
fi

# --- Schritt 4: nur wenn wirklich etwas Neues dazugekommen ist ---------------
if [ "$ANY_IMPORTED" = "0" ]; then
    echo ""
    echo "$(date): Keine neue Periode importiert — VPS-Requeue wird übersprungen."
    [ "$HAD_ERROR" = "1" ] && exit 1
    exit 0
fi

if run_vps_resets "$IMPORTED_PERIODS"; then
    rm -f "$PENDING_RESET_MARKER"
else
    # Ohne Marker bliebe ein gescheiterter Reset unbemerkt liegen: der Import
    # gilt dann als erledigt, aber niemand scrapt den neuen Monat nach.
    echo "$IMPORTED_PERIODS" > "$PENDING_RESET_MARKER"
    echo "$(date): Reset vorgemerkt ($PENDING_RESET_MARKER) — der nächste Lauf holt ihn nach."
fi

# --- Schritt 5: Datenprüfung (nur nach echtem Import) ------------------------
# Vollständigkeit + Elo-Plausibilität gegen die offizielle Liste, siehe
# qc/audit.py. Kein --until nötig: audit_data.py deckelt selbst auf
# den Vormonat, die gerade importierte Liste zählt also noch nicht als Lücke.
# --store legt den Lauf in qc_audit_runs ab (QC-Bereich im Dashboard, Migration 021).
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
    --store --source monthly_update \
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
echo "$(date): inkl. Listen-Gruppen für Neuzugänge und Reaktivierte."
echo "$(date): ========================================"
[ "$HAD_ERROR" = "1" ] && exit 1
exit 0
