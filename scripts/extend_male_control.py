#!/usr/bin/env python3
"""Extend male_control group with additional age-matched, active men.

Adds N more players to male_control without touching existing assignments.
Uses a different random seed (default: 43) to ensure disjoint sampling.
"""

import argparse
import logging
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import config, get_database_url
from scripts.seed_players import (
    age_matched_sample,
    load_players_from_file,
)

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Extend male_control group")
    parser.add_argument("--n", type=int, default=150, help="Additional players (default: 150)")
    parser.add_argument("--seed", type=int, default=43,
                        help="Random seed — different from initial seed 42 (default: 43)")
    parser.add_argument("--file", type=str, help="Path to FIDE TXT file")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    args = parser.parse_args()

    groups_cfg = config["groups"]
    ft_cfg = groups_cfg["female_top"]
    min_rating = ft_cfg["min_rating"]
    max_rating = ft_cfg["max_rating"]

    filepath = Path(args.file) if args.file else Path(config["data"]["players_file"])
    if not filepath.is_absolute():
        filepath = Path(__file__).resolve().parent.parent / filepath

    all_players = load_players_from_file(filepath)

    conn = psycopg2.connect(
        get_database_url(),
        options="-c statement_timeout=900000",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fide_id FROM players WHERE analysis_group = 'male_control'")
            existing_ids = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT fide_id FROM players WHERE analysis_group = 'female_top'")
            female_ids = {r[0] for r in cur.fetchall()}

        logger.info("Existing male_control: %d players", len(existing_ids))

        # Reference distribution: ALL women in the rating range from the TXT
        # (matches the logic used for the initial 130 sample)
        women = [
            p for p in all_players
            if p["sex"] == "F"
            and min_rating <= (p["std_rating"] or 0) <= max_rating
        ]
        # Pool: active men in range, not already in male_control
        men_pool = [
            p for p in all_players
            if p["sex"] == "M"
            and p["active"]
            and min_rating <= (p["std_rating"] or 0) <= max_rating
            and p["fide_id"] not in existing_ids
            and p["fide_id"] not in female_ids
        ]
        logger.info(
            "Reference: %d women; available active men (excl. existing): %d",
            len(women), len(men_pool),
        )

        sampled = age_matched_sample(women, men_pool, args.n, args.seed)
        new_ids = [m["fide_id"] for m in sampled]
        logger.info("Sampled %d new men", len(new_ids))

        if args.dry_run:
            print("DRY-RUN — would add:")
            for m in sampled[:10]:
                print(f"  {m['fide_id']:>10}  {m['name']:<40} "
                      f"{m['federation']}  rating={m['std_rating']}  "
                      f"birth={m['birth_year']}")
            print(f"  ... ({len(sampled)} total)")
            return

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE players SET analysis_group = 'male_control' "
                    "WHERE fide_id = ANY(%s) AND analysis_group IS NULL",
                    (new_ids,),
                )
                added = cur.rowcount
                cur.execute(
                    "SELECT COUNT(*) FROM players WHERE analysis_group = 'male_control'"
                )
                total = cur.fetchone()[0]

        print(f"\n{'='*50}")
        print(f"Extension complete:")
        print(f"  Added:             {added}")
        print(f"  Total male_control: {total}")
        print(f"  Seed used:         {args.seed}")
        print(f"{'='*50}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
