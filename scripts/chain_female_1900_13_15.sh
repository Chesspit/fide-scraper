#!/usr/bin/env bash
# Kette: female_1900_13 → 14 → 15
# Jede Gruppe startet automatisch nach Abschluss der vorherigen.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FROM="2012-08-01"
TO="2026-05-01"
PSQL="/opt/homebrew/Cellar/libpq/18.3/bin/psql"
DB_URL="postgresql://fide:nimzo194.@localhost:5434/fidedb"

SCRAPE_GROUPS=(female_1900_13 female_1900_14 female_1900_15)

for GROUP in "${SCRAPE_GROUPS[@]}"; do
    echo ""
    echo "========================================"
    echo "$(date): Starte $GROUP"
    echo "========================================"

    bash "$SCRIPT_DIR/scripts/run_local_backfill.sh" "$GROUP" "$FROM" "$TO"

    "$PSQL" "$DB_URL" -c "UPDATE groups SET backfill_status='complete' WHERE group_name='$GROUP';" 2>/dev/null \
        && echo "$(date): $GROUP → groups.backfill_status = complete" \
        || echo "$(date): WARNUNG: groups-Update für $GROUP fehlgeschlagen"
done

echo ""
echo "========================================"
echo "$(date): Kette female_1900_13–15 abgeschlossen."
echo "========================================"
