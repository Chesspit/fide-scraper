#!/usr/bin/env bash
# Kette: female_1800_20 → 21 → 22 → 23 → 24
# Setzt die female_1800-Serie nach Abschluss von 01–19 fort (letzte Gruppe der Serie 1800–2199).
# Jede Gruppe startet automatisch nach Abschluss der vorherigen.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FROM="2010-01-01"
TO="2026-04-01"
PSQL="/opt/homebrew/Cellar/libpq/18.3/bin/psql"
DB_URL="postgresql://fide:nimzo194.@localhost:5434/fidedb"

SCRAPE_GROUPS=(female_1800_20 female_1800_21 female_1800_22 female_1800_23 female_1800_24)

for GROUP in "${SCRAPE_GROUPS[@]}"; do
    echo ""
    echo "========================================"
    echo "$(date): Starte $GROUP"
    echo "========================================"

    bash "$SCRIPT_DIR/scripts/run_local_backfill.sh" "$GROUP" "$FROM" "$TO"

    "$PSQL" "$DB_URL" -c "UPDATE groups SET backfill_status='complete', scraped_from='$FROM', scraped_to='$TO' WHERE group_name='$GROUP';" 2>/dev/null \
        && echo "$(date): $GROUP → groups.backfill_status = complete" \
        || echo "$(date): WARNUNG: groups-Update für $GROUP fehlgeschlagen"
done

echo ""
echo "========================================"
echo "$(date): Kette female_1800_20–24 abgeschlossen — female_1800-Serie (1800–2199) komplett."
echo "========================================"
