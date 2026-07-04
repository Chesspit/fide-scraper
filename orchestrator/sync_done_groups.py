#!/usr/bin/env python3
"""Bulk-sync der Queue: Gruppen auf 'done' setzen, die in PG bereits voll gescrapt sind.

Seit Review #5 liegen Queue (orchestrator.scrape_groups) und Scrape-Daten
(public.scrape_periods/players) in derselben PostgreSQL — eine Verbindung,
search_path=orchestrator,public (siehe setup_db.connect).

Usage:
    python orchestrator/sync_done_groups.py [--dry-run]
"""

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.setup_db import connect
from scraper.db import is_valid_fide_period


def valid_periods_for_year(year: int) -> list[str]:
    today = date.today()
    cutoff = date(today.year, today.month - 1, 1) if today.month > 1 else date(today.year - 1, 12, 1)
    return [
        date(year, m, 1).isoformat()
        for m in range(1, 13)
        if date(year, m, 1) <= cutoff and is_valid_fide_period(date(year, m, 1))
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()

    # 1. Load all pending groups from the queue
    cur.execute(
        "SELECT id, federation, year, elo_min, elo_max FROM scrape_groups WHERE status = 'pending'"
    )
    rows = cur.fetchall()
    print(f"Pending groups in queue: {len(rows)}")

    # 2. Precompute valid periods per year
    year_periods: dict[int, list[str]] = {}
    for _, _, year, _, _ in rows:
        if year not in year_periods:
            year_periods[year] = valid_periods_for_year(year)

    # 3. Load scraped (fide_id, period) from PG into a set — one query
    print("Loading scraped periods from PostgreSQL...")
    cur.execute(
        "SELECT fide_id, period FROM scrape_periods WHERE status IN ('ok', 'no_data')"
    )
    scraped = set((r[0], r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1])) for r in cur.fetchall())
    print(f"  {len(scraped):,} scraped player-periods found")

    # 4. Load active players grouped by (federation, std_rating) from PG
    print("Loading active players from PostgreSQL...")
    cur.execute(
        "SELECT fide_id, federation, std_rating FROM players WHERE active = TRUE AND std_rating IS NOT NULL"
    )
    players = cur.fetchall()
    print(f"  {len(players):,} active players")

    # Index: federation -> list of (fide_id, rating)
    fed_players: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for fide_id, fed, rating in players:
        fed_players[fed].append((fide_id, rating))

    # 5. Check each pending group
    mark_done: list[int] = []
    mark_skipped: list[int] = []

    for group_id, federation, year, elo_min, elo_max in rows:
        periods = year_periods.get(year, [])
        if not periods:
            mark_skipped.append(group_id)
            continue

        fide_ids = [
            fid for fid, rating in fed_players.get(federation, [])
            if elo_min <= rating <= elo_max
        ]
        if not fide_ids:
            mark_skipped.append(group_id)
            continue

        # All expected combos scraped?
        all_done = all(
            (fid, p) in scraped
            for fid in fide_ids
            for p in periods
        )
        if all_done:
            mark_done.append(group_id)

    print(f"\nGroups to mark as done:    {len(mark_done):,}")
    print(f"Groups to mark as skipped: {len(mark_skipped):,}")
    print(f"Groups remaining pending:  {len(rows) - len(mark_done) - len(mark_skipped):,}")

    if args.dry_run:
        print("\n[dry-run] No changes written.")
        conn.close()
        return

    if mark_done:
        cur.execute(
            "UPDATE scrape_groups SET status='done', last_run_at=localtimestamp WHERE id = ANY(%s)",
            (mark_done,),
        )
    if mark_skipped:
        cur.execute(
            "UPDATE scrape_groups SET status='skipped', notes='no active players or no valid periods' WHERE id = ANY(%s)",
            (mark_skipped,),
        )
    print("\nDone. Queue updated.")
    conn.close()


if __name__ == "__main__":
    main()
