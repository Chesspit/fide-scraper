#!/usr/bin/env bash
# Kette: female_1900_09 → 10 → 11 → 12
# Jede Gruppe startet automatisch nach Abschluss der vorherigen.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FROM="2012-08-01"
TO="2026-05-01"
PSQL="/opt/homebrew/Cellar/libpq/18.3/bin/psql"
DB_URL="postgresql://fide:nimzo194.@localhost:5434/fidedb"

SCRAPE_GROUPS=(female_1900_09 female_1900_10 female_1900_11 female_1900_12)

for GROUP in "${SCRAPE_GROUPS[@]}"; do
    echo ""
    echo "========================================"
    echo "$(date): Starte $GROUP"
    echo "========================================"

    bash "$SCRIPT_DIR/scripts/run_local_backfill.sh" "$GROUP" "$FROM" "$TO"

    # groups-Tabelle auf complete setzen
    "$PSQL" "$DB_URL" -c "UPDATE groups SET backfill_status='complete' WHERE group_name='$GROUP';" 2>/dev/null \
        && echo "$(date): $GROUP → groups.backfill_status = complete" \
        || echo "$(date): WARNUNG: groups-Update für $GROUP fehlgeschlagen"
done

echo ""
echo "========================================"
echo "$(date): Kette female_1900_09–12 abgeschlossen."
echo "========================================"
