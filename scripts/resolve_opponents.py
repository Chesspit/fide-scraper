#!/usr/bin/env python3
"""Resolve opponent_fide_id in game_results by looking up name + federation in players table."""

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import get_database_url

logger = logging.getLogger(__name__)


def build_candidate_map(conn, period: str | None) -> dict:
    """Return {(name, federation): [(fide_id, std_rating), ...]}.

    Single batch query joins unresolved opponents against the players table.
    """
    where_clause = "WHERE gr.opponent_fide_id IS NULL"
    params: list = []
    if period:
        where_clause += " AND gr.period = %s"
        params.append(period)

    sql = f"""
        WITH opps AS (
            SELECT DISTINCT opponent_name, opponent_federation
            FROM game_results gr
            {where_clause}
        )
        SELECT o.opponent_name, o.opponent_federation, p.fide_id, p.std_rating
        FROM opps o
        JOIN players p
          ON p.name = o.opponent_name
         AND p.federation = o.opponent_federation
    """
    candidates: dict[tuple, list] = defaultdict(list)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for name, fed, fide_id, std_rating in cur.fetchall():
            candidates[(name, fed)].append((fide_id, std_rating))
    return candidates


def resolve_opponents(conn, period: str | None = None, dry_run: bool = False):
    """Resolve opponent_fide_id for game_results rows where it's NULL.

    Strategy:
    1. Unique (name, federation) match → assign directly
    2. Multiple matches → pick candidate with closest std_rating (no tolerance limit)
    3. No match → leave NULL
    """
    where_clause = "WHERE gr.opponent_fide_id IS NULL"
    params: list = []
    if period:
        where_clause += " AND gr.period = %s"
        params.append(period)

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM game_results gr {where_clause}", params
        )
        total_unresolved = cur.fetchone()[0]

    logger.info("Unresolved game_results: %d", total_unresolved)
    if total_unresolved == 0:
        return

    logger.info("Building candidate map from players table...")
    candidates = build_candidate_map(conn, period)
    logger.info("  %d unique (name, federation) tuples with ≥1 candidate", len(candidates))

    logger.info("Fetching unresolved game_results...")
    with conn.cursor() as cur:
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
    unresolvable = 0
    not_found = 0
    updates: list[tuple[int, int]] = []
    no_match_samples: list[tuple] = []
    wide_match_samples: list[tuple] = []

    for row_id, opp_name, opp_fed, opp_rating in unresolved:
        key = (opp_name, opp_fed)
        cands = candidates.get(key, [])

        if len(cands) == 1:
            updates.append((cands[0][0], row_id))
            resolved += 1
        elif len(cands) > 1:
            best_id = None
            best_diff = float("inf")
            for fide_id, std_rating in cands:
                if std_rating is None or opp_rating is None:
                    continue
                diff = abs(std_rating - opp_rating)
                if diff < best_diff:
                    best_id = fide_id
                    best_diff = diff
            if best_id is not None:
                updates.append((best_id, row_id))
                resolved += 1
                # Flag wide matches for post-hoc review (e.g. diff > 200 looks suspicious)
                if best_diff > 200 and len(wide_match_samples) < 10:
                    wide_match_samples.append(
                        (opp_name, opp_fed, opp_rating, len(cands), best_diff)
                    )
            else:
                unresolvable += 1
        else:
            not_found += 1
            if len(no_match_samples) < 10:
                no_match_samples.append((opp_name, opp_fed, opp_rating))

    if not dry_run and updates:
        logger.info("Applying %d updates...", len(updates))
        with conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE game_results SET opponent_fide_id = %s WHERE id = %s",
                    updates,
                    page_size=1000,
                )

    action = "Would update" if dry_run else "Updated"
    pct = resolved / total_unresolved * 100 if total_unresolved else 0
    print(f"\nResults:")
    print(f"  Total unresolved: {total_unresolved}")
    print(f"  {action}:     {resolved} ({pct:.1f}%)")
    print(f"  Unresolvable:     {unresolvable}")
    print(f"  Not found:        {not_found}")

    if wide_match_samples:
        print("\n  Sample wide-gap matches (rating diff > 200, review if suspicious):")
        for name, fed, rating, n, diff in wide_match_samples:
            print(f"    {name} ({fed}, {rating}) — {n} candidates, best diff={diff}")

    if no_match_samples:
        print("\n  Sample no-match:")
        for name, fed, rating in no_match_samples:
            print(f"    {name} ({fed}, {rating})")


def main():
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
