#!/usr/bin/env python3
"""FIDE Calculations Scraper — CLI entry point."""

import argparse
import logging
import sys
from datetime import date, timedelta

from scraper.config import config
from scraper.db import get_connection, get_pending_periods, save_period, save_period_no_data
from scraper.fetcher import fetch_calculations, sleep_between_requests
from scraper.parser import parse_calculations

logger = logging.getLogger("scraper")


def get_latest_period() -> str:
    """Return the first day of the most recently completed month."""
    today = date.today()
    first_of_month = today.replace(day=1)
    last_month = first_of_month - timedelta(days=1)
    return last_month.replace(day=1).isoformat()


def generate_period_range(from_date: str, to_date: str) -> list[str]:
    """Generate all monthly periods between from_date and to_date (inclusive)."""
    start = date.fromisoformat(from_date).replace(day=1)
    end = date.fromisoformat(to_date).replace(day=1)
    periods = []
    current = start
    while current <= end:
        periods.append(current.isoformat())
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return periods


def resolve_periods(args) -> list[str]:
    """Resolve period list from CLI args or config."""
    if getattr(args, "latest", False):
        return [get_latest_period()]

    if getattr(args, "periods", None):
        return args.periods

    cfg = config["periods"]
    mode = cfg.get("mode", "latest")

    if mode == "latest":
        return [get_latest_period()]
    elif mode == "range":
        return generate_period_range(cfg["from"], cfg["to"])
    elif mode == "list":
        return cfg["list"]
    else:
        logger.error("Unknown periods mode: %s", mode)
        sys.exit(1)


def cmd_run(args):
    periods = resolve_periods(args)
    fide_ids = getattr(args, "fide_ids", None)
    backfill = getattr(args, "backfill", False)

    conn = get_connection()
    try:
        pending = get_pending_periods(conn, periods, fide_ids)
        total = len(pending)

        if total == 0:
            logger.info("Nothing to scrape — all periods already processed.")
            return

        logger.info("Scraping %d player-period combinations...", total)

        for i, (fide_id, period) in enumerate(pending, 1):
            period_str = period.isoformat() if isinstance(period, date) else period
            try:
                logger.info("[%d/%d] fide_id=%s period=%s", i, total, fide_id, period_str)
                html = fetch_calculations(fide_id, period_str)

                if not html or not html.strip():
                    save_period_no_data(conn, fide_id, period_str)
                    logger.info("  → no data")
                    continue

                games, k_factor, own_rating = parse_calculations(
                    html, fide_id, period_str
                )

                if not games:
                    save_period_no_data(conn, fide_id, period_str)
                    logger.info("  → no games parsed")
                    continue

                save_period(conn, fide_id, period_str, games, k_factor, own_rating)
                logger.info("  → %d games, K=%s, Ro=%s", len(games), k_factor, own_rating)

            except Exception:
                logger.exception(
                    "  → ERROR for fide_id=%s period=%s", fide_id, period_str
                )

            sleep_between_requests(backfill=backfill)

    finally:
        conn.close()

    logger.info("Done.")


def cmd_status(args):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.analysis_group,
                    sp.status,
                    COUNT(*) AS cnt,
                    COUNT(DISTINCT sp.fide_id) AS players,
                    COUNT(DISTINCT sp.period) AS periods
                FROM scrape_periods sp
                JOIN players p USING (fide_id)
                WHERE p.analysis_group IS NOT NULL
                GROUP BY p.analysis_group, sp.status
                ORDER BY p.analysis_group, sp.status
                """
            )
            rows = cur.fetchall()

        if not rows:
            print("No scrape data yet.")
            return

        print(f"{'Group':<15} {'Status':<10} {'Count':>7} {'Players':>8} {'Periods':>8}")
        print("-" * 52)
        for group, status, cnt, players, periods in rows:
            print(f"{group:<15} {status:<10} {cnt:>7} {players:>8} {periods:>8}")
    finally:
        conn.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="FIDE Calculations Scraper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = subparsers.add_parser("run", help="Scrape calculations data")
    run_parser.add_argument("--periods", nargs="+", help="Period(s) to scrape (YYYY-MM-01)")
    run_parser.add_argument("--latest", action="store_true", help="Scrape latest period only")
    run_parser.add_argument("--fide-ids", nargs="+", type=int, help="Specific FIDE IDs")
    run_parser.add_argument("--backfill", action="store_true", help="Use slower rate limit")
    run_parser.set_defaults(func=cmd_run)

    # status
    status_parser = subparsers.add_parser("status", help="Show scrape progress")
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
