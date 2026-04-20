#!/usr/bin/env python3
"""Resolve opponent_fide_id in game_results by looking up name + federation in players.

Two-stage matching strategy:

1. **Exact match (normalized name + federation)** — applied without rating tolerance
   when a single candidate exists. Handles the common case where the opponent played
   under the same federation they currently hold.
2. **Fed-fallback (normalized name only)** — applied when step 1 misses, for
   opponents whose federation changed (Caruana ITA→USA, Aronian ARM→USA, Svidler
   RUS→FID, etc.). Requires the candidate's rating at the time of play to be within
   `--fed-fallback-tolerance` (default 100) of the game's opponent_rating.

Period-accurate rating disambiguation:
    When multiple candidates share the same normalized name, we compare each
    candidate's `rating_history.published_rating` at the snapshot period closest
    to the game's period against the game's `opponent_rating`. This eliminates
    rating drift and cuts false matches dramatically vs. the old approach that
    compared against `players.std_rating` (current April-2026 rating). Falls
    back to `players.std_rating` when rating_history has no entry.

Name normalization (lowercase, drop commas, collapse whitespace) handles the
FIDE-TXT convention differences that plague Indian names: calc-HTML writes
"Gukesh, D" while the TXT has "Gukesh D" — same player, different format.
"""

import argparse
import logging
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import date
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


def build_rating_history_index(conn, fide_ids: set[int]) -> dict[int, tuple[list, list]]:
    """Load rating_history.published_rating for the given fide_ids.

    Returns `{fide_id: (sorted_periods, ratings_parallel)}`. The two lists are
    aligned by index and sorted by period ascending, so we can use bisect_left
    to find the closest snapshot period for any game period in O(log n).
    """
    if not fide_ids:
        return {}

    by_fid: dict[int, list] = defaultdict(list)
    with conn.cursor(name="rh_stream") as cur:
        cur.itersize = 50000
        cur.execute(
            """
            SELECT fide_id, period, published_rating
            FROM rating_history
            WHERE published_rating IS NOT NULL
              AND fide_id = ANY(%s)
            """,
            (list(fide_ids),),
        )
        for fide_id, period, rating in cur:
            by_fid[fide_id].append((period, rating))

    result: dict[int, tuple[list, list]] = {}
    for fide_id, entries in by_fid.items():
        entries.sort()
        periods = [e[0] for e in entries]
        ratings = [e[1] for e in entries]
        result[fide_id] = (periods, ratings)
    return result


def rating_at_period(
    rh_index: dict[int, tuple[list, list]],
    fide_id: int,
    game_period: date,
    current_rating: int | None,
) -> int | None:
    """Return the candidate's rating at the snapshot period closest to game_period.

    Uses binary search over the sorted snapshot periods for this fide_id. Falls
    back to the player's current std_rating when no rating_history entry exists.
    """
    entry = rh_index.get(fide_id)
    if not entry:
        return current_rating
    periods, ratings = entry
    if not periods:
        return current_rating

    idx = bisect_left(periods, game_period)
    # Compare idx-1 and idx, pick the closer one
    best = None
    best_gap = None
    for probe in (idx - 1, idx):
        if 0 <= probe < len(periods):
            gap = abs((periods[probe] - game_period).days)
            if best_gap is None or gap < best_gap:
                best = ratings[probe]
                best_gap = gap
    return best if best is not None else current_rating


def pick_closest_period_aware(
    cands: list[tuple],
    opp_rating: int | None,
    game_period: date,
    rh_index: dict[int, tuple[list, list]],
    max_diff: int | None = None,
) -> tuple[int | None, float]:
    """From candidates [(fide_id, std_rating, ...)], pick the one whose
    period-accurate rating is closest to `opp_rating`.

    For each candidate, looks up the rating_history snapshot nearest to
    game_period and uses that for the comparison. Falls back to std_rating
    when no rating_history entry exists.

    Returns (fide_id, diff). If `max_diff` is set and the best diff exceeds
    it, returns (None, diff).
    """
    if opp_rating is None:
        return None, float("inf")

    best_id = None
    best_diff = float("inf")
    for cand in cands:
        fide_id, std_rating = cand[0], cand[1]
        rating_then = rating_at_period(rh_index, fide_id, game_period, std_rating)
        if rating_then is None:
            continue
        diff = abs(rating_then - opp_rating)
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
    fed_fallback_tolerance: int = 100,
    exact_tolerance: int | None = None,
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

    # Collect fide_ids we might need period-accurate ratings for:
    # every candidate in any bucket a normalization could hit.
    relevant_fids: set[int] = set()
    for cands in by_name_fed.values():
        if len(cands) > 1:
            relevant_fids.update(c[0] for c in cands)
    for cands in by_name.values():
        relevant_fids.update(c[0] for c in cands)

    logger.info("Loading rating_history for %d candidate fide_ids...", len(relevant_fids))
    rh_index = build_rating_history_index(conn, relevant_fids)
    logger.info("  %d fide_ids have rating_history entries", len(rh_index))

    logger.info("Fetching unresolved game_results...")
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT gr.id, gr.opponent_name, gr.opponent_federation, gr.opponent_rating, gr.period
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

    for row_id, opp_name, opp_fed, opp_rating, game_period in unresolved:
        norm = normalize_name(opp_name)

        # Stage 1: exact (name + federation)
        cands = by_name_fed.get((norm, opp_fed), [])
        if cands:
            if len(cands) == 1:
                updates.append((cands[0][0], row_id))
                resolved_exact += 1
                continue

            best_id, best_diff = pick_closest_period_aware(
                cands, opp_rating, game_period, rh_index, max_diff=exact_tolerance
            )
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

        # Stage 2: fed-fallback (name only, rating-bounded, period-aware)
        cands_any_fed = by_name.get(norm, [])
        if not cands_any_fed:
            not_found += 1
            if len(no_match_samples) < 15:
                no_match_samples.append((opp_name, opp_fed, opp_rating))
            continue

        best_id, best_diff = pick_closest_period_aware(
            cands_any_fed, opp_rating, game_period, rh_index,
            max_diff=fed_fallback_tolerance,
        )
        if best_id is None:
            not_found += 1
            if len(no_match_samples) < 15:
                no_match_samples.append((opp_name, opp_fed, opp_rating))
            continue

        updates.append((best_id, row_id))
        resolved_fallback += 1
        if len(fallback_samples) < 15:
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
        default=100,
        help="Max |rating diff| when matching across federations (default 100, "
             "period-accurate ratings make this tight)",
    )
    parser.add_argument(
        "--exact-tolerance",
        type=int,
        default=None,
        help="Max |rating diff| for exact (name+fed) matches with multiple candidates. "
             "Default: no limit (pick closest).",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(get_database_url())
    try:
        resolve_opponents(
            conn,
            period=args.period,
            dry_run=args.dry_run,
            fed_fallback_tolerance=args.fed_fallback_tolerance,
            exact_tolerance=args.exact_tolerance,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
