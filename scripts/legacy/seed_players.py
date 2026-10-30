#!/usr/bin/env python3
"""Import FIDE player list into database with age-matched sampling for control group.

⚠️ EINGEFROREN (Stand 16.09.2026) — für neue Spieler NICHT mehr fortführen.

Dieses Skript befüllt System A (`groups`-Tabelle + `players.analysis_group`), die
kuratierten Gruppen aus der ursprünglichen Forschungsfrage. Sie wachsen nicht mit:
Das laufende Scraping steuert ausschließlich System B (`orchestrator.scrape_groups`,
erzeugt von orchestrator/generate_groups.py), und worker.py::get_fide_ids() fragt
`players` live nach Föderation + std_rating ab, ohne analysis_group je anzufassen.
Siehe CLAUDE.md, Abschnitt „Gruppen: zwei Systeme, nur eines davon lebt".

Die bestehenden Gruppen sind zudem nur teilweise befüllt (`backfill_status='partial'`,
female_top 23 von 66, male_control 48 von 649 — mit active=TRUE bleiben 2 bzw. 4,
siehe docs/project_status.md 6.7). Sie bleiben als Methoden-Dokumentation und als
Vergleichsachse für coverage_by_analysis_group erhalten, taugen aber nicht als
Analysegrundlage. Neue Kohorten dynamisch aus rating_history ableiten — Vorbild ist
notebooks/_generate_14.py.
"""

import argparse
import logging
import random
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras


from core.fide_list import load_players_from_file
from scraper.config import config, get_database_url

logger = logging.getLogger(__name__)

def decade_bucket(birth_year: int | None) -> int | None:
    if birth_year is None:
        return None
    return (birth_year // 10) * 10


def age_matched_sample(
    women: list[dict],
    men: list[dict],
    sample_size: int,
    seed: int,
) -> list[dict]:
    """Sample men proportionally to the birth-decade distribution of women."""
    # Build decade distribution of women
    women_decades = defaultdict(int)
    for w in women:
        d = decade_bucket(w["birth_year"])
        if d:
            women_decades[d] += 1

    total_women_with_decade = sum(women_decades.values())
    if total_women_with_decade == 0:
        logger.warning("No women with birth year — falling back to random sample")
        random.seed(seed)
        return random.sample(men, min(sample_size, len(men)))

    # Calculate slots per decade
    slots = {}
    assigned = 0
    sorted_decades = sorted(women_decades.keys())
    for d in sorted_decades[:-1]:
        n = round(sample_size * women_decades[d] / total_women_with_decade)
        slots[d] = n
        assigned += n
    # Last decade gets the remainder
    slots[sorted_decades[-1]] = sample_size - assigned

    # Group men by decade
    men_by_decade = defaultdict(list)
    for m in men:
        d = decade_bucket(m["birth_year"])
        if d:
            men_by_decade[d].append(m)

    # Sample
    random.seed(seed)
    sampled = []
    overflow = 0

    for d in sorted_decades:
        target = slots.get(d, 0) + overflow
        available = men_by_decade.get(d, [])
        if len(available) <= target:
            sampled.extend(available)
            overflow = target - len(available)
        else:
            sampled.extend(random.sample(available, target))
            overflow = 0

    # If still overflow, sample from remaining unsampled men
    if overflow > 0:
        sampled_ids = {m["fide_id"] for m in sampled}
        remaining = [m for m in men if m["fide_id"] not in sampled_ids]
        extra = min(overflow, len(remaining))
        if extra > 0:
            sampled.extend(random.sample(remaining, extra))

    logger.info("Sampled %d men (target: %d)", len(sampled), sample_size)

    # Log decade distribution
    sampled_decades = defaultdict(int)
    for m in sampled:
        d = decade_bucket(m["birth_year"])
        if d:
            sampled_decades[d] += 1

    logger.info("Decade distribution comparison:")
    logger.info("  Decade   Women  Men(sampled)")
    for d in sorted(set(list(women_decades.keys()) + list(sampled_decades.keys()))):
        logger.info("  %ds   %4d   %4d", d, women_decades.get(d, 0), sampled_decades.get(d, 0))

    return sampled


def bulk_upsert_players(conn, players: list[dict], batch_size: int = 5000):
    """Bulk upsert players using executemany in batches."""
    sql = """
        INSERT INTO players (fide_id, name, federation, title, women_title,
                             sex, birth_year, std_rating, active, updated_at)
        VALUES (%(fide_id)s, %(name)s, %(federation)s, %(title)s, %(women_title)s,
                %(sex)s, %(birth_year)s, %(std_rating)s, %(active)s, NOW())
        ON CONFLICT (fide_id) DO UPDATE SET
            name = EXCLUDED.name,
            federation = EXCLUDED.federation,
            title = EXCLUDED.title,
            women_title = EXCLUDED.women_title,
            sex = EXCLUDED.sex,
            birth_year = EXCLUDED.birth_year,
            std_rating = EXCLUDED.std_rating,
            active = EXCLUDED.active,
            updated_at = NOW()
    """
    total = 0
    with conn:
        with conn.cursor() as cur:
            for i in range(0, len(players), batch_size):
                batch = players[i : i + batch_size]
                psycopg2.extras.execute_batch(cur, sql, batch, page_size=1000)
                total += len(batch)
                logger.info("  Upserted %d / %d players", total, len(players))
    return total


def set_analysis_groups(conn, female_ids: list[int], male_ids: list[int]):
    """Set analysis_group for female_top/male_control (legacy sampling path)."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE players SET analysis_group = NULL "
                "WHERE analysis_group IN ('female_top', 'male_control')"
            )
            if female_ids:
                cur.execute(
                    "UPDATE players SET analysis_group = 'female_top' WHERE fide_id = ANY(%s)",
                    (female_ids,),
                )
                logger.info("Set %d players as female_top", cur.rowcount)
            if male_ids:
                cur.execute(
                    "UPDATE players SET analysis_group = 'male_control' WHERE fide_id = ANY(%s)",
                    (male_ids,),
                )
                logger.info("Set %d players as male_control", cur.rowcount)


def set_generic_group(conn, group_name: str, fide_ids: list[int]):
    """Assign analysis_group for a full-population group without touching other groups."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE players SET analysis_group = NULL WHERE analysis_group = %s",
                (group_name,),
            )
            if fide_ids:
                cur.execute(
                    "UPDATE players SET analysis_group = %s WHERE fide_id = ANY(%s)",
                    (group_name, fide_ids),
                )
                logger.info("Set %d players as %s", cur.rowcount, group_name)


def update_groups_table(conn, group_name: str, player_count: int):
    """Update player_count in groups table after seeding."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE groups SET player_count = %s, updated_at = NOW() WHERE group_name = %s",
                (player_count, group_name),
            )
            if cur.rowcount == 0:
                logger.warning("Group '%s' not found in groups table — skipping update", group_name)


def load_group_definition(conn, group_name: str) -> dict | None:
    """Load group definition (elo_min, elo_max, federations, sex) from groups table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT elo_min, elo_max, federations, sampling, backfill_from, backfill_to, sex "
            "FROM groups WHERE group_name = %s",
            (group_name,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "elo_min":       row[0],
        "elo_max":       row[1],
        "federations":   row[2],  # e.g. "SUI" or "SUI,AUT,GER" or None
        "sampling":      row[3],
        "backfill_from": row[4],
        "backfill_to":   row[5],
        "sex":           (row[6] or "").strip() or None,  # 'F', 'M', or None
    }


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Seed FIDE players into database")
    parser.add_argument("--group", type=str,
                        help="Group name to assign (e.g. female_top, global_02, sui_01, dach_01)")
    parser.add_argument("--federation", type=str,
                        help="Comma-separated federation codes to filter by (e.g. SUI or SUI,AUT,GER)")
    parser.add_argument("--n", type=int, help="Sample size for male_control (sampling mode only)")
    parser.add_argument("--min-rating", type=int, help="Minimum ELO rating (inclusive)")
    parser.add_argument("--max-rating", type=int, help="Maximum ELO rating (inclusive)")
    parser.add_argument("--seed", type=int, help="Random seed for age-matched sampling")
    parser.add_argument("--file", type=str, help="Path to FIDE TXT file")
    parser.add_argument("--refresh-metadata", action="store_true",
                        help="Only upsert player metadata (active flag, ratings, etc.); "
                             "do not touch analysis_group assignments")
    args = parser.parse_args()

    # Resolve config
    groups_cfg = config["groups"]
    ft_cfg = groups_cfg["female_top"]
    mc_cfg = groups_cfg["male_control"]

    filepath = Path(args.file) if args.file else Path(config["data"]["players_file"])
    if not filepath.is_absolute():
        filepath = Path(__file__).resolve().parent.parent / filepath

    if not filepath.exists():
        logger.error("File not found: %s", filepath)
        sys.exit(1)

    all_players = load_players_from_file(filepath)

    conn = psycopg2.connect(
        get_database_url(),
        options="-c statement_timeout=900000",
    )
    try:
        # Always upsert all players unless seeding a specific group
        if not args.group or args.refresh_metadata:
            logger.info("Importing all %d players into database...", len(all_players))
            bulk_upsert_players(conn, all_players)

        if args.refresh_metadata:
            active_count = sum(1 for p in all_players if p["active"])
            print(f"\n{'='*50}")
            print(f"Metadata refresh complete:")
            print(f"  Total players upserted: {len(all_players):,}")
            print(f"  Active:   {active_count:,}")
            print(f"  Inactive: {len(all_players) - active_count:,}")
            print(f"  analysis_group assignments left untouched.")
            print(f"{'='*50}")
            return

        if not args.group:
            return

        group_name = args.group
        is_sampling_group = group_name in ("female_top", "male_control")

        # ── Full-population path (global_XX, dach_XX, sui_XX, aut_XX, ger_XX, …) ──
        if not is_sampling_group:
            # Load definition from groups table; CLI args override
            grp_def = load_group_definition(conn, group_name)
            if not grp_def:
                logger.error("Group '%s' not found in groups table. Add it first.", group_name)
                sys.exit(1)

            min_rating = args.min_rating if args.min_rating is not None else grp_def["elo_min"]
            max_rating = args.max_rating if args.max_rating is not None else grp_def["elo_max"]

            if min_rating is None or max_rating is None:
                logger.error("No elo_min/elo_max in groups table for '%s'. Use --min-rating/--max-rating.", group_name)
                sys.exit(1)

            fed_str_raw = args.federation or grp_def["federations"]
            federations = (
                [f.strip().upper() for f in fed_str_raw.split(",")]
                if fed_str_raw else None
            )

            sex_filter = grp_def.get("sex")  # 'F', 'M', or None
            candidates = [
                p for p in all_players
                if p["active"]
                and min_rating <= (p["std_rating"] or 0) <= max_rating
                and (federations is None or p["federation"] in federations)
                and (sex_filter is None or p.get("sex") == sex_filter)
            ]

            fide_ids = [p["fide_id"] for p in candidates]
            set_generic_group(conn, group_name, fide_ids)
            update_groups_table(conn, group_name, len(fide_ids))

            fed_str = ",".join(federations) if federations else "alle"
            print(f"\n{'='*50}")
            print(f"Seed complete: {group_name}")
            print(f"  Spieler:     {len(fide_ids)}")
            print(f"  ELO-Range:   {min_rating}–{max_rating}")
            print(f"  Federations: {fed_str}")
            print(f"{'='*50}")
            return

        # ── Sampling path (female_top / male_control) ────────────────────────────
        min_rating = args.min_rating or ft_cfg["min_rating"]
        max_rating = args.max_rating or ft_cfg["max_rating"]
        sample_size = args.n or mc_cfg["sample_size"]
        seed = args.seed or mc_cfg["sampling"]["seed"]

        women = [
            p for p in all_players
            if p["sex"] == "F" and min_rating <= (p["std_rating"] or 0) <= max_rating
        ]
        men = [
            p for p in all_players
            if p["sex"] == "M" and min_rating <= (p["std_rating"] or 0) <= max_rating
        ]
        logger.info("Rating range %d-%d: %d women, %d men", min_rating, max_rating, len(women), len(men))

        female_ids = []
        male_ids = []

        if group_name == "female_top":
            female_ids = [w["fide_id"] for w in women]
        if group_name == "male_control":
            sampled_men = age_matched_sample(women, men, sample_size, seed)
            male_ids = [m["fide_id"] for m in sampled_men]

        set_analysis_groups(conn, female_ids, male_ids)

        print(f"\n{'='*50}")
        print(f"Seed complete:")
        print(f"  female_top:   {len(female_ids)} players")
        print(f"  male_control: {len(male_ids)} players")
        print(f"  Rating range: {min_rating}-{max_rating}")
        print(f"  Random seed:  {seed}")
        print(f"{'='*50}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
