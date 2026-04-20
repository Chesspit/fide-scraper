#!/usr/bin/env python3
"""Resolve opponent_fide_id in game_results by looking up name + federation in players.

Two-stage matching strategy:

1. **Exact match (normalized name + federation)** — applied without rating tolerance,
   like before. Handles the common case where the opponent played under the same
   federation they currently hold.
2. **Fed-fallback (normalized name only)** — applied when step 1 misses, for
   opponents whose federation changed (Caruana ITA→USA, Aronian ARM→USA, Svidler
   RUS→FID, etc.). Requires the candidate's current std_rating to be within
   `--fed-fallback-tolerance` (default 200) of the rating at the time of play to
   guard against coincidental namesakes in other federations.

Name normalization (lowercase, drop commas, collapse whitespace) also handles the
FIDE-TXT convention differences that plague Indian names: calc-HTML writes
"Gukesh, D" while the TXT has "Gukesh D" — same player, different format.
"""

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


def normalize_name(name: str | None) -> str:
    """Lowercase, strip commas, collapse whitespace.

    Matches "Gukesh, D" ↔ "Gukesh D", "Sethuraman, S.p." ↔ "Sethuraman, S.P.".
    """
    if not name:
        return ""
    return " ".join(name.replace(",", "").lower().split())


def build_candidate_maps(conn) -> tuple[dict, dict]:
    """Load the players table once, return two lookup maps.

    - `by_name_fed`: {(normalized_name, federation): [(fide_id, std_rating), ...]}
    - `by_name`:     {normalized_name: [(fide_id, std_rating, federation), ...]}

    Loading 1.8M rows into memory is cheaper than N per-row lookups.
    """
    by_name_fed: dict = defaultdict(list)
    by_name: dict = defaultdict(list)

    with conn.cursor(name="players_stream") as cur:
        cur.itersize = 50000
        cur.execute(
            "SELECT fide_id, name, federation, std_rating FROM players WHERE name IS NOT NULL"
        )
        for fide_id, name, fed, rating in cur:
            norm = normalize_name(name)
            if not norm:
                continue
            if fed:
                by_name_fed[(norm, fed)].append((fide_id, rating))
            by_name[norm].append((fide_id, rating, fed))

    return by_name_fed, by_name


def pick_closest(
    cands: list[tuple],
    opp_rating: int | None,
    max_diff: int | None = None,
) -> tuple[int | None, float]:
    """From candidates [(fide_id, std_rating, ...)], pick the one with std_rating
    closest to `opp_rating`.

    Returns (fide_id, diff). If `max_diff` is set and the best diff exceeds it,
    returns (None, diff).
    """
    best_id = None
    best_diff = float("inf")
    for cand in cands:
        fide_id, std_rating = cand[0], cand[1]
        if std_rating is None or opp_rating is None:
            continue
        diff = abs(std_rating - opp_rating)
        if diff < best_diff:
            best_id = fide_id
            best_diff = diff
    if best_id is not None and max_diff is not None and best_diff > max_diff:
        return None, best_diff
    return best_id, best_diff


def resolve_opponents(
    conn,
    period: str | None = None,
    dry_run: bool = False,
    fed_fallback_tolerance: int = 200,
):
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

    logger.info("Loading players table and building normalized candidate maps...")
    by_name_fed, by_name = build_candidate_maps(conn)
    logger.info(
        "  %d (name, fed) buckets, %d name-only buckets",
        len(by_name_fed),
        len(by_name),
    )

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

    resolved_exact = 0
    resolved_fallback = 0
    unresolvable = 0
    not_found = 0
    updates: list[tuple[int, int]] = []

    wide_match_samples: list[tuple] = []
    fallback_samples: list[tuple] = []
    no_match_samples: list[tuple] = []

    for row_id, opp_name, opp_fed, opp_rating in unresolved:
        norm = normalize_name(opp_name)

        # Stage 1: exact (name + federation)
        cands = by_name_fed.get((norm, opp_fed), [])
        if cands:
            if len(cands) == 1:
                updates.append((cands[0][0], row_id))
                resolved_exact += 1
                continue

            best_id, best_diff = pick_closest(cands, opp_rating)
            if best_id is not None:
                updates.append((best_id, row_id))
                resolved_exact += 1
                if best_diff > 200 and len(wide_match_samples) < 10:
                    wide_match_samples.append(
                        (opp_name, opp_fed, opp_rating, len(cands), best_diff)
                    )
                continue
            unresolvable += 1
            continue

        # Stage 2: fed-fallback (name only, rating-bounded)
        cands_any_fed = by_name.get(norm, [])
        if not cands_any_fed:
            not_found += 1
            if len(no_match_samples) < 15:
                no_match_samples.append((opp_name, opp_fed, opp_rating))
            continue

        best_id, best_diff = pick_closest(
            cands_any_fed, opp_rating, max_diff=fed_fallback_tolerance
        )
        if best_id is None:
            not_found += 1
            if len(no_match_samples) < 15:
                no_match_samples.append((opp_name, opp_fed, opp_rating))
            continue

        updates.append((best_id, row_id))
        resolved_fallback += 1
        if len(fallback_samples) < 15:
            # Record the candidate's federation for inspection
            matched_fed = next(
                (fed for fid, _, fed in cands_any_fed if fid == best_id), None
            )
            fallback_samples.append(
                (opp_name, opp_fed, opp_rating, matched_fed, best_diff)
            )

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
    total_resolved = resolved_exact + resolved_fallback
    pct = total_resolved / total_unresolved * 100 if total_unresolved else 0
    print(f"\nResults:")
    print(f"  Total unresolved:      {total_unresolved}")
    print(f"  {action} (exact):      {resolved_exact}")
    print(f"  {action} (fed-fallback): {resolved_fallback} (tolerance ±{fed_fallback_tolerance})")
    print(f"  {action} (total):      {total_resolved} ({pct:.1f}%)")
    print(f"  Unresolvable:          {unresolvable}")
    print(f"  Not found:             {not_found}")

    if wide_match_samples:
        print("\n  Sample wide-gap exact matches (rating diff > 200, review if suspicious):")
        for name, fed, rating, n, diff in wide_match_samples:
            print(f"    {name} ({fed}, {rating}) — {n} candidates, best diff={diff}")

    if fallback_samples:
        print("\n  Sample fed-fallback matches (candidate federation differs):")
        for name, fed, rating, matched_fed, diff in fallback_samples:
            print(f"    {name} ({fed}→{matched_fed}, {rating}) — diff={diff}")

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
    parser.add_argument(
        "--fed-fallback-tolerance",
        type=int,
        default=200,
        help="Max |rating diff| when matching across federations (default 200)",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(get_database_url())
    try:
        resolve_opponents(
            conn,
            period=args.period,
            dry_run=args.dry_run,
            fed_fallback_tolerance=args.fed_fallback_tolerance,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
