#!/usr/bin/env python3
"""Import historical FIDE TXT files into rating_history.published_rating for validation."""

import argparse
import logging
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import get_database_url
from scripts.seed_players import detect_columns_from_header, parse_player_line

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FILE_PATTERN = re.compile(r"players_list_foa_(\d{4})-(\d{2})\.txt")


def period_from_filename(filepath: Path) -> str | None:
    """Extract period (YYYY-MM-01) from filename like players_list_foa_2025-06.txt."""
    match = FILE_PATTERN.search(filepath.name)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-01"


def find_snapshot_files(data_dir: Path) -> list[tuple[Path, str]]:
    """Find all snapshot files in data_dir and extract their periods."""
    results = []
    for f in sorted(data_dir.glob("players_list_foa_*.txt")):
        period = period_from_filename(f)
        if period:
            results.append((f, period))
    return results


def import_snapshot(conn, filepath: Path, period: str):
    """Import published ratings from a single TXT file for analysis players."""
    logger.info("Importing %s (period=%s)", filepath.name, period)

    # Get analysis player IDs
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fide_id FROM players WHERE analysis_group IS NOT NULL"
        )
        analysis_ids = {row[0] for row in cur.fetchall()}

    if not analysis_ids:
        logger.warning("No analysis players found — run seed_players.py first")
        return

    # Parse file
    with open(filepath, encoding="latin-1") as f:
        header = f.readline()
        columns = detect_columns_from_header(header)

        updates = []
        for line in f:
            p = parse_player_line(line, columns)
            if p and p["fide_id"] in analysis_ids and p["std_rating"]:
                updates.append((p["fide_id"], period, p["std_rating"]))

    if not updates:
        logger.info("  No matching players found in file")
        return

    # Upsert into rating_history
    sql = """
        INSERT INTO rating_history (fide_id, period, published_rating)
        VALUES (%s, %s, %s)
        ON CONFLICT (fide_id, period)
        DO UPDATE SET published_rating = EXCLUDED.published_rating
    """
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, updates, page_size=500)

    logger.info("  Imported published_rating for %d players", len(updates))


def show_validation(conn):
    """Show mismatches between std_rating (from scraping) and published_rating (from TXT)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fide_id, period, std_rating, published_rating,
                   std_rating - published_rating AS diff
            FROM rating_history
            WHERE std_rating IS NOT NULL AND published_rating IS NOT NULL
              AND std_rating != published_rating
            ORDER BY ABS(std_rating - published_rating) DESC
            LIMIT 20
        """)
        rows = cur.fetchall()

    if not rows:
        print("\nValidation: No mismatches between scraped and published ratings.")
        return

    print(f"\nValidation: {len(rows)} mismatches found (showing top 20):")
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
        description="Import FIDE TXT snapshots into rating_history for validation"
    )
    parser.add_argument("--file", type=str, help="Import a specific file")
    parser.add_argument("--validate", action="store_true",
                        help="Show mismatches after import")
    args = parser.parse_args()

    conn = psycopg2.connect(get_database_url())
    try:
        if args.file:
            filepath = Path(args.file)
            period = period_from_filename(filepath)
            if not period:
                logger.error("Cannot extract period from filename: %s", filepath.name)
                logger.error("Expected format: players_list_foa_YYYY-MM.txt")
                sys.exit(1)
            import_snapshot(conn, filepath, period)
        else:
            snapshots = find_snapshot_files(DATA_DIR)
            if not snapshots:
                logger.info("No snapshot files found in %s", DATA_DIR)
                logger.info("Expected: players_list_foa_YYYY-MM.txt")
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
