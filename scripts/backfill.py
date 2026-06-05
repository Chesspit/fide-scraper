#!/usr/bin/env python3
"""Backfill historical FIDE calculations data."""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.db import (
    ensure_connection,
    get_connection,
    get_pending_periods,
    save_period,
    save_period_no_data,
)
from scraper.fetcher import (
    BlockedError,
    RateLimitedError,
    RATE_LIMIT_PAUSE_SECONDS,
    fetch_calculations,
    sleep_between_requests,
)
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
    parser.add_argument("--group", nargs="+", metavar="GROUP",
                        help="Only scrape specific groups: female_top male_control "
                             "elite_2600 female_2200 swiss_2026 (space-separated)")
    parser.add_argument("--fide-ids-file", metavar="FILE",
                        help="Datei mit einer FIDE-ID pro Zeile (Alternative zu --fide-ids)")
    parser.add_argument("--shard", metavar="N/M",
                        help="Process only shard N of M (e.g. --shard 1/2 or --shard 2/2). "
                             "Uses round-robin split so both shards finish at the same time. "
                             "Run on different machines to avoid increased load on one IP.")
    parser.add_argument("--reverse", action="store_true",
                        help="Scrape periods newest-first (2026-03 → from_date). "
                             "Useful to get recent data first and appear less suspicious to FIDE.")
    args = parser.parse_args()

    if args.fide_ids_file:
        with open(args.fide_ids_file) as f:
            file_ids = [int(l.strip()) for l in f if l.strip()]
        args.fide_ids = (args.fide_ids or []) + file_ids

    # Parse shard argument
    shard_n, shard_m = 1, 1
    if args.shard:
        try:
            shard_n, shard_m = (int(x) for x in args.shard.split("/"))
            if not (1 <= shard_n <= shard_m) or shard_m < 1:
                raise ValueError
        except ValueError:
            parser.error("--shard must be N/M with 1 ≤ N ≤ M (e.g. --shard 1/2)")

    periods = generate_period_range(args.from_date, args.to_date)
    logger.info("Backfill range: %s to %s (%d periods%s)",
                args.from_date, args.to_date, len(periods),
                ", REVERSE" if args.reverse else "")

    conn = get_connection()
    try:
        pending = get_pending_periods(conn, periods, args.fide_ids, args.group)
        if args.reverse:
            pending = list(reversed(pending))

        # Apply round-robin shard split: shard N/M takes every M-th item starting at N-1
        if shard_m > 1:
            pending = pending[shard_n - 1 :: shard_m]
            logger.info("Shard %d/%d: %d of %d total pending periods",
                        shard_n, shard_m, len(pending), len(pending) * shard_m)

        total = len(pending)

        if total == 0:
            logger.info("Nothing to backfill — all periods already processed.")
            return

        # Pre-filter: skip periods where num_games=0 in rating_history (player had no games).
        # Only skip if num_games IS NOT NULL — NULL means no TXT snapshot available, so we
        # must scrape to find out. This avoids HTTP requests for known-empty months.
        with conn.cursor() as cur:
            fide_ids = list({fid for fid, _ in pending})
            periods  = list({p   for _,  p in pending})
            cur.execute(
                """SELECT fide_id, period FROM rating_history
                   WHERE fide_id = ANY(%s) AND period = ANY(%s) AND num_games = 0""",
                (fide_ids, periods)
            )
            skip_set = {(r[0], r[1]) for r in cur.fetchall()}

        if skip_set:
            # Batch-insert alle no_data-Einträge auf einmal — viel schneller als einzeln
            with conn.cursor() as cur:
                import psycopg2.extras
                rows = [
                    (fid, p.isoformat() if hasattr(p, "isoformat") else p)
                    for fid, p in skip_set
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO scrape_periods (fide_id, period, status, scraped_at)
                       VALUES %s ON CONFLICT (fide_id, period) DO NOTHING""",
                    [(fid, period, "no_data", "NOW()") for fid, period in rows],
                )
            conn.commit()
            logger.info("Pre-filter: %d periods skipped (num_games=0 in TXT snapshot)",
                        len(skip_set))
            pending = [(fid, p) for fid, p in pending if (fid, p) not in skip_set]

        total = len(pending)  # nach Pre-Filter aktualisieren
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

            except RateLimitedError:
                logger.warning(
                    "  → HTTP 429 RATE LIMITED — pausing %d minutes before continuing",
                    RATE_LIMIT_PAUSE_SECONDS // 60,
                )
                conn = save_period_no_data(conn, fide_id, period_str, http_status=429)
                errors += 1
                time.sleep(RATE_LIMIT_PAUSE_SECONDS)
                logger.info("Resuming after rate-limit pause.")
                continue

            except BlockedError:
                logger.error(
                    "  → HTTP 403 BLOCKED — IP appears to be blocked by FIDE. "
                    "Stopping scraper. Check your IP/VPN and try again later."
                )
                conn = save_period_no_data(conn, fide_id, period_str, http_status=403)
                logger.info("Backfill interrupted. %d/%d done, %d errors.", i - 1, total, errors)
                return

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
