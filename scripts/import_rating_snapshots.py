#!/usr/bin/env python3
"""Import historical FIDE snapshots into the database.

Two effects on the DB:

1. `players` table: INSERT-only for fide_ids that are not yet known. This fills the
   gaps for deceased or long-inactive players (Landa †2022, Bukavshin †2016, etc.)
   who are no longer in the current April-2026 snapshot but appear as opponents in
   older games. **Existing rows are never overwritten** — the current snapshot
   remains the authoritative source for active-player metadata.

2. `rating_history.published_rating`: one row per (fide_id, snapshot period). This
   gives the resolver period-accurate ratings for disambiguation, which is
   dramatically better than comparing current std_rating against an opponent rating
   from five years ago.

Supported input file formats:

- `players_list_foa_YYYY-MM.txt` / `.zip` (current convention)
- `standard_{mmm}{YY}frl.txt` / `.zip` (original FIDE archive format, e.g.
  `standard_feb15frl.zip` → 2015-02-01, `standard_jan26frl.zip` → 2026-01-01)

ZIP files are streamed without extracting to disk.
"""

import argparse
import logging
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import get_database_url
from scripts.seed_players import (
    detect_columns_from_header,
    open_player_list,
    parse_player_line,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CURRENT_PATTERN = re.compile(r"players_list_foa_(\d{4})-(\d{2})\.(?:txt|zip)$", re.IGNORECASE)
FIDE_PATTERN = re.compile(r"standard_([a-z]{3})(\d{2})frl\.(?:txt|zip)$", re.IGNORECASE)

MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def period_from_filename(filepath: Path) -> str | None:
    """Extract period (YYYY-MM-01) from filename, supporting both naming conventions."""
    name = filepath.name
    m = CURRENT_PATTERN.search(name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    m = FIDE_PATTERN.search(name)
    if m:
        month = MONTH_MAP.get(m.group(1).lower())
        if not month:
            return None
        year = 2000 + int(m.group(2))
        return f"{year}-{month}-01"
    return None


def find_snapshot_files(data_dir: Path) -> list[tuple[Path, str]]:
    """Find all snapshot files in data_dir and extract their periods."""
    results = []
    seen = set()
    # Sort by period (ascending) via extracted date
    for f in sorted(list(data_dir.glob("*.txt")) + list(data_dir.glob("*.zip"))):
        period = period_from_filename(f)
        if not period or period in seen:
            continue
        seen.add(period)
        results.append((f, period))
    results.sort(key=lambda x: x[1])
    return results


def parse_snapshot(filepath: Path) -> list[dict]:
    """Parse a snapshot file (TXT or ZIP) and return all player dicts."""
    lines = open_player_list(filepath)
    header = next(lines)
    columns = detect_columns_from_header(header)

    players = []
    for line in lines:
        p = parse_player_line(line.rstrip("\n\r"), columns)
        if p:
            players.append(p)
    return players


def insert_new_players(conn, players: list[dict]) -> int:
    """INSERT fide_ids that don't exist yet. Returns number of rows actually inserted.

    Uses ON CONFLICT DO NOTHING so existing rows (from the current snapshot) are never
    overwritten. This only adds players whose records have since been removed from
    FIDE's current list.
    """
    if not players:
        return 0

    sql = """
        INSERT INTO players (fide_id, name, federation, title, women_title,
                             sex, birth_year, std_rating, active, updated_at)
        VALUES %s
        ON CONFLICT (fide_id) DO NOTHING
    """
    # We mark these as inactive=True ONLY if the snapshot says so; but since these
    # players don't appear in the current snapshot, they're effectively inactive now.
    # We still preserve the active flag from the historical snapshot for reference.
    rows = [
        (
            p["fide_id"],
            p["name"],
            p["federation"],
            p["title"],
            p["women_title"],
            p["sex"],
            p["birth_year"],
            p["std_rating"] or None,
            p["active"],
        )
        for p in players
    ]

    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
                page_size=5000,
            )
            inserted = cur.rowcount
    return max(inserted, 0)


def upsert_rating_history(conn, players: list[dict], period: str) -> int:
    """Upsert published_rating for every fide_id in the snapshot."""
    rows = [
        (p["fide_id"], period, p["std_rating"])
        for p in players
        if p["std_rating"]
    ]
    if not rows:
        return 0

    sql = """
        INSERT INTO rating_history (fide_id, period, published_rating)
        VALUES %s
        ON CONFLICT (fide_id, period)
        DO UPDATE SET published_rating = EXCLUDED.published_rating
    """
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, rows, page_size=5000)
    return len(rows)


def import_snapshot(conn, filepath: Path, period: str):
    logger.info("Reading %s (period=%s)...", filepath.name, period)
    players = parse_snapshot(filepath)
    logger.info("  Parsed %d players", len(players))

    inserted = insert_new_players(conn, players)
    logger.info("  players: %d newly inserted (existing rows untouched)", inserted)

    upserted = upsert_rating_history(conn, players, period)
    logger.info("  rating_history: %d rows written for period=%s", upserted, period)


def show_validation(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fide_id, period, std_rating, published_rating,
                   std_rating - published_rating AS diff
            FROM rating_history
            WHERE std_rating IS NOT NULL AND published_rating IS NOT NULL
              AND std_rating != published_rating
            ORDER BY ABS(std_rating - published_rating) DESC
            LIMIT 20
            """
        )
        rows = cur.fetchall()

    if not rows:
        print("\nValidation: No mismatches between scraped and published ratings.")
        return

    print(f"\nValidation: {len(rows)} mismatches (showing top 20):")
    print(f"{'FIDE ID':>10} {'Period':<12} {'Scraped':>8} {'Published':>10} {'Diff':>6}")
    print("-" * 50)
    for fide_id, period, std, pub, diff in rows:
        print(f"{fide_id:>10} {period!s:<12} {std:>8} {pub:>10} {diff:>+6}")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Import historical FIDE snapshots into players + rating_history"
    )
    parser.add_argument("--file", type=str, help="Import a specific file")
    parser.add_argument(
        "--validate", action="store_true", help="Show scraped-vs-published mismatches"
    )
    args = parser.parse_args()

    conn = psycopg2.connect(get_database_url())
    try:
        if args.file:
            filepath = Path(args.file)
            period = period_from_filename(filepath)
            if not period:
                logger.error("Cannot extract period from filename: %s", filepath.name)
                logger.error(
                    "Expected: players_list_foa_YYYY-MM.{txt,zip} or standard_mmmYYfrl.{txt,zip}"
                )
                sys.exit(1)
            import_snapshot(conn, filepath, period)
        else:
            snapshots = find_snapshot_files(DATA_DIR)
            if not snapshots:
                logger.info("No snapshot files found in %s", DATA_DIR)
                sys.exit(0)

            logger.info("Found %d snapshot files", len(snapshots))
            for filepath, period in snapshots:
                import_snapshot(conn, filepath, period)

        if args.validate:
            show_validation(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
