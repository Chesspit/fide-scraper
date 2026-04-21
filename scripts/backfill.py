#!/usr/bin/env python3
"""Backfill historical FIDE calculations data."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.db import (
    ensure_connection,
    get_connection,
    get_pending_periods,
    save_period,
    save_period_no_data,
)
from scraper.fetcher import fetch_calculations, sleep_between_requests
from scraper.main import generate_period_range
from scraper.parser import parse_calculations

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Backfill historical calculations data")
    parser.add_argument("--from", dest="from_date", required=True,
                        help="Start period (YYYY-MM-01)")
    parser.add_argument("--to", dest="to_date", required=True,
                        help="End period (YYYY-MM-01)")
    parser.add_argument("--fide-ids", nargs="+", type=int,
                        help="Specific FIDE IDs (default: all analysis players)")
    args = parser.parse_args()

    periods = generate_period_range(args.from_date, args.to_date)
    logger.info("Backfill range: %s to %s (%d periods)", args.from_date, args.to_date, len(periods))

    conn = get_connection()
    try:
        pending = get_pending_periods(conn, periods, args.fide_ids)
        total = len(pending)

        if total == 0:
            logger.info("Nothing to backfill — all periods already processed.")
            return

        logger.info("Backfilling %d player-period combinations...", total)

        errors = 0
        for i, (fide_id, period) in enumerate(pending, 1):
            period_str = period.isoformat() if hasattr(period, "isoformat") else period
            try:
                logger.info("[%d/%d] fide_id=%s period=%s", i, total, fide_id, period_str)
                html = fetch_calculations(fide_id, period_str)

                if not html or not html.strip():
                    conn = save_period_no_data(conn, fide_id, period_str)
                    logger.info("  → no data")
                    continue

                games, k_factor, own_rating = parse_calculations(html, fide_id, period_str)

                if not games:
                    conn = save_period_no_data(conn, fide_id, period_str)
                    logger.info("  → no games parsed")
                    continue

                conn = save_period(conn, fide_id, period_str, games, k_factor, own_rating)
                logger.info("  → %d games, K=%s, Ro=%s", len(games), k_factor, own_rating)

            except Exception:
                errors += 1
                logger.exception("  → ERROR for fide_id=%s period=%s", fide_id, period_str)
                conn = ensure_connection(conn)

            sleep_between_requests(backfill=True)

        logger.info("Backfill complete. %d/%d succeeded, %d errors.", total - errors, total, errors)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
