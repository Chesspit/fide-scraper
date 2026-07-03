#!/usr/bin/env python3
"""Reset done P1/P2/P3 monthly-refresh scrape_groups back to pending.

Ersetzt das frühere reset_current_year.py (2026-07 gelöscht, siehe
Git-Historie) — der bisherige Reset setzte pauschal ALLE done-Gruppen des
Jahres zurück, was versehentlich auch den völlig separaten, laufenden
Welt-Backfill (dc_ae/de/es/hk/in/mx/uk/us/dach, echte Föderationscodes)
mit-requeuete. Dieses Skript trifft ausschließlich die drei Tier-Sentinel
('P1'/'P2'/'P3' in der federation-Spalte, siehe monthly_refresh_tiers.py)
und lässt jede andere Zeile unangetastet.

Zieht außerdem das year-Feld der P1/P2/P3-Gruppen auf das aktuelle Jahr nach
(No-Op außer beim Jahreswechsel im Januar) — worker.valid_periods_for_year()
würde sonst beim Rollover keine neuen Perioden mehr finden.

Verwendung:
    python3 orchestrator/reset_monthly_refresh.py
    python3 orchestrator/reset_monthly_refresh.py --year 2026
    python3 orchestrator/reset_monthly_refresh.py --dry-run
"""

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.monthly_refresh_tiers import TIERS
from orchestrator.setup_db import DB_PATH

_TIER_PLACEHOLDERS = ",".join("?" * len(TIERS))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Done P1/P2/P3-Monatsrefresh-Gruppen zurücksetzen (nicht den Welt-Backfill)"
    )
    parser.add_argument("--year", type=int, default=date.today().year,
                        help="Jahr, auf das P1/P2/P3-Gruppen gezogen werden (default: aktuelles Jahr)")
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
        f"SELECT COUNT(*) FROM scrape_groups WHERE federation IN ({_TIER_PLACEHOLDERS}) AND status = 'done'",
        TIERS,
    )
    done_count = cur.fetchone()[0]
    cur.execute(
        f"SELECT COUNT(*) FROM scrape_groups WHERE federation IN ({_TIER_PLACEHOLDERS}) AND year != ?",
        (*TIERS, args.year),
    )
    stale_year_count = cur.fetchone()[0]

    print(f"P1/P2/P3: {done_count} done-Gruppen gefunden, {stale_year_count} mit Jahr != {args.year}.")

    if done_count == 0 and stale_year_count == 0:
        print("Nichts zu tun.")
        conn.close()
        return 0

    if args.dry_run:
        cur.execute(
            f"SELECT federation, elo_min, elo_max, records_found, year FROM scrape_groups "
            f"WHERE federation IN ({_TIER_PLACEHOLDERS}) AND status = 'done' "
            f"ORDER BY federation, elo_min LIMIT 15",
            TIERS,
        )
        rows = cur.fetchall()
        print(f"Beispiele (erste 15 von {done_count}):")
        for r in rows:
            print(f"  {r['federation']}  ELO {r['elo_min']:4d}–{r['elo_max']:4d}"
                  f"  ({r['records_found'] or 0} Partien, Jahr {r['year']})")
        print("--dry-run: keine Änderungen vorgenommen.")
        conn.close()
        return 0

    cur.execute(
        f"UPDATE scrape_groups SET year = ? WHERE federation IN ({_TIER_PLACEHOLDERS})",
        (args.year, *TIERS),
    )
    year_updated = cur.rowcount

    cur.execute(
        f"UPDATE scrape_groups SET status = 'pending', last_run_at = NULL "
        f"WHERE federation IN ({_TIER_PLACEHOLDERS}) AND status = 'done'",
        TIERS,
    )
    reset_count = cur.rowcount

    conn.commit()
    print(f"{year_updated} P1/P2/P3-Gruppen auf Jahr {args.year} gezogen.")
    print(f"{reset_count} Gruppen auf pending zurückgesetzt.")
    print("Historischer Welt-Backfill (dc_ae/de/es/hk/in/mx/uk/us/dach) unangetastet.")
    print("dc_update_1..3 holen den neuen Monat beim nächsten Lauf automatisch nach.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
