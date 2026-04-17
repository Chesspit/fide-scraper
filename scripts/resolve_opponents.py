#!/usr/bin/env python3
"""Resolve opponent_fide_id in game_results by looking up name + federation in players table."""

import argparse
import logging
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import get_database_url

logger = logging.getLogger(__name__)


def resolve_opponents(conn, period: str | None = None, dry_run: bool = False):
    """Resolve opponent_fide_id for game_results rows where it's NULL.

    Lookup strategy:
    1. Exact match on name + federation → if unique, assign
    2. Multiple matches → pick closest rating (within ±50)
    3. No match → leave NULL
    """
    where_clause = "WHERE gr.opponent_fide_id IS NULL"
    params = []
    if period:
        where_clause += " AND gr.period = %s"
        params.append(period)

    with conn.cursor() as cur:
        # Count unresolved
        cur.execute(
            f"SELECT COUNT(*) FROM game_results gr {where_clause}", params
        )
        total_unresolved = cur.fetchone()[0]
        logger.info("Unresolved opponent_fide_id: %d", total_unresolved)

        if total_unresolved == 0:
            return

        # Fetch unresolved rows
        cur.execute(
            f"""
            SELECT gr.id, gr.opponent_name, gr.opponent_federation, gr.opponent_rating
            FROM game_results gr
            {where_clause}
            """,
            params,
        )
        unresolved = cur.fetchall()

    resolved = 0
    ambiguous = 0
    not_found = 0
    updates = []

    with conn.cursor() as cur:
        for row_id, opp_name, opp_fed, opp_rating in unresolved:
            if not opp_name:
                not_found += 1
                continue

            # Strategy 1: exact name + federation match
            if opp_fed:
                cur.execute(
                    "SELECT fide_id, std_rating FROM players WHERE name = %s AND federation = %s",
                    (opp_name, opp_fed),
                )
            else:
                cur.execute(
                    "SELECT fide_id, std_rating FROM players WHERE name = %s",
                    (opp_name,),
                )

            matches = cur.fetchall()

            if len(matches) == 1:
                updates.append((matches[0][0], row_id))
                resolved += 1
            elif len(matches) > 1 and opp_rating:
                # Strategy 2: pick closest rating within ±50
                best = None
                best_diff = 999
                for fide_id, rating in matches:
                    if rating:
                        diff = abs(rating - opp_rating)
                        if diff < best_diff:
                            best = fide_id
                            best_diff = diff
                if best and best_diff <= 50:
                    updates.append((best, row_id))
                    resolved += 1
                else:
                    ambiguous += 1
            elif len(matches) > 1:
                ambiguous += 1
            else:
                not_found += 1

    if not dry_run and updates:
        with conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE game_results SET opponent_fide_id = %s WHERE id = %s",
                    updates,
                    page_size=1000,
                )

    action = "Would update" if dry_run else "Updated"
    print(f"\nResults:")
    print(f"  Total unresolved: {total_unresolved}")
    print(f"  {action}:         {resolved}")
    print(f"  Ambiguous:        {ambiguous}")
    print(f"  Not found:        {not_found}")
    print(f"  Resolution rate:  {resolved / total_unresolved * 100:.1f}%")


def main():
    import psycopg2.extras  # noqa: ensure available

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Resolve opponent FIDE IDs via name+federation lookup"
    )
    parser.add_argument("--period", type=str, help="Only resolve for this period (YYYY-MM-01)")
    parser.add_argument("--dry-run", action="store_true", help="Count only, don't write")
    args = parser.parse_args()

    conn = psycopg2.connect(get_database_url())
    try:
        resolve_opponents(conn, period=args.period, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
