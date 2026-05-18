#!/usr/bin/env python3
"""Import FIDE player list into database with age-matched sampling for control group."""

import argparse
import logging
import random
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterator

import psycopg2
import psycopg2.extras

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import config, get_database_url

logger = logging.getLogger(__name__)

# Historical FIDE lists use month-based rating column labels (e.g. "FEB15", "JAN26").
# Current lists use "SRtng". The detector below tries "SRtng" first, then the pattern.
MONTH_RATING_PATTERN = re.compile(
    r"\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}\b",
    re.IGNORECASE  # pre-2015 FIDE files use lowercase (e.g. "sep12")
)

# Column positions in FIDE fixed-width TXT file (0-indexed, verified April 2026)
DEFAULT_COLUMNS = {
    "id": (0, 15),
    "name": (15, 76),
    "federation": (76, 79),
    "sex": (80, 81),
    "title": (84, 87),
    "women_title": (89, 92),
    "std_rating": (113, 118),
    "birth_year": (152, 156),
    "flag": (158, 162),
}


def detect_columns_from_header(header_line: str) -> dict:
    """Try to detect column positions from the header line.

    Handles three formats:
    - Current (SRtng rating col, Sex/WTit present)
    - Historical 2012-2015 (month-based rating col e.g. FEB15, Sex/WTit present)
    - Pre-2013 legacy (month-based e.g. sep09, NO Sex/WTit, shorter layout)
    Falls back to DEFAULT_COLUMNS if detection fails.
    """
    # ── Pre-2013 legacy format: "ID number" (lowercase n), no Sex/WTit ──────
    # Header example: "ID number Name                              TitlFed  Sep09 GamesBorn  Flag"
    if "ID number" in header_line:
        cols = {
            "id":          (0,  9),
            "name":        (10, 44),
            "title":       (44, 47),
            "federation":  (48, 51),
            "birth_year":  (64, 68),
            "flag":        (70, 74),
        }
        m = MONTH_RATING_PATTERN.search(header_line)
        if m:
            cols["std_rating"] = (m.start(), m.start() + 5)
            # "GamesBorn" in pre-2013: Games immediately follows the rating (5 chars wide)
            games_pos = header_line.find("Games")
            if games_pos >= 0:
                cols["std_games"] = (games_pos, games_pos + 5)
            logger.info("Detected pre-2013 column layout (rating col: %s, no Sex/WTit): %s",
                        m.group(), cols)
            return cols
        logger.warning("Pre-2013 header found but no rating column detected")
        return DEFAULT_COLUMNS

    # ── Current and 2012-2015 formats ────────────────────────────────────────
    cols = {}
    markers = {
        "id": "ID Number",
        "name": "Name",
        "federation": "Fed",
        "sex": "Sex",
        "title": "Tit",
        "women_title": "WTit",
        "birth_year": "B-day",
        "flag": "Flag",
    }
    widths = {
        "id": 15, "name": 61, "federation": 3, "sex": 1,
        "title": 3, "women_title": 3, "birth_year": 4, "flag": 4,
    }

    for key, marker in markers.items():
        pos = header_line.find(marker)
        if pos >= 0:
            cols[key] = (pos, pos + widths[key])

    # Rating column: prefer "SRtng" (current), fall back to month-based label
    rating_pos = header_line.find("SRtng")
    rating_marker = "SRtng"
    if rating_pos < 0:
        m = MONTH_RATING_PATTERN.search(header_line)
        if m:
            rating_pos = m.start()
            rating_marker = m.group()
    if rating_pos >= 0:
        cols["std_rating"] = (rating_pos, rating_pos + 5)

    # Standard Games played this period — column name changed over the years:
    # Pre-2013: "Games", 2013-2025: "Gms", 2026+: "SGm"
    for games_label, width in [("SGm", 4), ("Gms", 4), ("Games", 5)]:
        gpos = header_line.find(games_label)
        if gpos >= 0:
            cols["std_games"] = (gpos, gpos + width)
            break

    if len(cols) >= 6:
        logger.info("Detected column positions (rating col: %s): %s", rating_marker, cols)
        return cols

    logger.warning("Could not detect all columns from header, using defaults")
    return DEFAULT_COLUMNS


def open_player_list(filepath: Path) -> Iterator[str]:
    """Yield text lines from a FIDE player list (TXT or ZIP containing a TXT)."""
    if filepath.suffix.lower() == ".zip":
        with zipfile.ZipFile(filepath) as zf:
            inner_names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not inner_names:
                raise ValueError(f"No .txt file inside {filepath}")
            with zf.open(inner_names[0]) as fp:
                for raw in fp:
                    yield raw.decode("latin-1", errors="replace")
    else:
        with open(filepath, encoding="latin-1") as f:
            yield from f


def parse_player_line(line: str, columns: dict) -> dict | None:
    """Parse a single line from the FIDE TXT file into a player dict.

    Pre-2013 files have shorter lines and no Sex/WTit columns — those
    fields default to None and are handled gracefully.
    """
    # Pre-2013 lines can be as short as ~60 chars; newer format needs ~100.
    min_len = 55 if "sex" not in columns else 100
    if len(line) < min_len:
        return None

    def extract(key):
        start, end = columns[key]
        return line[start:end].strip() if len(line) > start else ""

    fide_id_str = extract("id")
    if not fide_id_str or not fide_id_str.isdigit():
        return None

    name = extract("name")
    if not name:
        return None

    rating_str = extract("std_rating") if "std_rating" in columns else ""
    try:
        std_rating = int(rating_str) if rating_str else 0
    except ValueError:
        std_rating = 0

    birth_str = extract("birth_year") if "birth_year" in columns else ""
    try:
        birth_year = int(birth_str) if birth_str and len(birth_str) == 4 else None
    except ValueError:
        birth_year = None

    title = extract("title") if "title" in columns else None
    title = title or None

    women_title = extract("women_title") if "women_title" in columns else None
    women_title = women_title or None

    flag = extract("flag").lower() if "flag" in columns else ""
    # FIDE flags: 'i' = inactive, 'wi' = woman inactive. Anything else → active.
    active = flag not in ("i", "wi")

    games_str = extract("std_games") if "std_games" in columns else ""
    try:
        std_games = int(games_str) if games_str else None
    except ValueError:
        std_games = None

    return {
        "fide_id": int(fide_id_str),
        "name": name,
        "federation": extract("federation") if "federation" in columns else None,
        "sex": extract("sex") if "sex" in columns else None,
        "title": title,
        "women_title": women_title,
        "std_rating": std_rating,
        "birth_year": birth_year,
        "active": active,
        "std_games": std_games,
    }


def load_players_from_file(filepath: Path) -> list[dict]:
    """Parse a FIDE TXT or ZIP file and return all player dicts."""
    logger.info("Reading %s ...", filepath)

    lines = open_player_list(filepath)
    header = next(lines)
    columns = detect_columns_from_header(header)

    players = []
    for line in lines:
        p = parse_player_line(line.rstrip("\n\r"), columns)
        if p:
            players.append(p)

    logger.info("Parsed %d players from file", len(players))
    return players


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
    """Load group definition (elo_min, elo_max, federations) from groups table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT elo_min, elo_max, federations, sampling, backfill_from, backfill_to "
            "FROM groups WHERE group_name = %s",
            (group_name,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "elo_min":      row[0],
        "elo_max":      row[1],
        "federations":  row[2],  # e.g. "SUI" or "SUI,AUT,GER" or None
        "sampling":     row[3],
        "backfill_from": row[4],
        "backfill_to":   row[5],
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

            candidates = [
                p for p in all_players
                if p["active"]
                and min_rating <= (p["std_rating"] or 0) <= max_rating
                and (federations is None or p["federation"] in federations)
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
