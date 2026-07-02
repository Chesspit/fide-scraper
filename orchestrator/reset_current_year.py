#!/usr/bin/env python3
"""SUPERSEDED (siehe Session-Plan "P1/P2/P3 ELO-Band-System"): dieser Reset
setzt PAUSCHAL alle done-Gruppen des Jahres zurück — das trifft ungewollt
auch den separaten, laufenden Welt-Backfill (dc_ae/de/es/hk/in/mx/uk/us/dach),
nicht nur die Update-Batches. Ersetzt durch orchestrator/reset_monthly_refresh.py,
das ausschließlich die P1/P2/P3-Tier-Gruppen trifft. Nicht mehr in
scripts/monthly_update.sh aufrufen.

Reset done scrape_groups for the current year back to pending.

Teil des monatlichen FIDE-Updates: sobald ein neuer Monat verfügbar ist,
werden done-Gruppen des laufenden Jahres auf pending zurückgesetzt.
Der Orchestrator-Worker holt den neuen Monat dann automatisch nach.
PostgreSQL scrape_periods sorgt dafür, dass bereits gescrapte Perioden
übersprungen werden — nur der neue Monat wird tatsächlich fetcht.

Verwendung:
    python3 orchestrator/reset_current_year.py
    python3 orchestrator/reset_current_year.py --year 2026
    python3 orchestrator/reset_current_year.py --dry-run
"""

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.setup_db import DB_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Done-Gruppen des laufenden Jahres zurücksetzen")
    parser.add_argument("--year", type=int, default=date.today().year,
                        help="Jahr der Gruppen (default: aktuelles Jahr)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur anzeigen, nicht ändern")
    args = parser.parse_args()

    db_path = DB_PATH
    if not db_path.exists():
        print(f"FEHLER: SQLite-DB nicht gefunden: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM scrape_groups WHERE year = ? AND status = 'done'",
        (args.year,),
    )
    count = cur.fetchone()[0]

    print(f"Jahr {args.year}: {count} done-Gruppen gefunden.")

    if count == 0:
        print("Nichts zu tun.")
        conn.close()
        return 0

    if args.dry_run:
        cur.execute(
            "SELECT federation, elo_min, elo_max, records_found FROM scrape_groups "
            "WHERE year = ? AND status = 'done' ORDER BY federation, elo_min LIMIT 15",
            (args.year,),
        )
        rows = cur.fetchall()
        print(f"Beispiele (erste 15 von {count}):")
        for r in rows:
            print(f"  {r['federation']:4s}  ELO {r['elo_min']:4d}–{r['elo_max']:4d}"
                  f"  ({r['records_found'] or 0} Partien)")
        print(f"--dry-run: keine Änderungen vorgenommen.")
        conn.close()
        return 0

    cur.execute(
        "UPDATE scrape_groups SET status = 'pending', last_run_at = NULL "
        "WHERE year = ? AND status = 'done'",
        (args.year,),
    )
    conn.commit()
    print(f"{cur.rowcount} Gruppen auf pending zurückgesetzt.")
    print("Worker holt neuen Monat beim nächsten Lauf automatisch nach.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
