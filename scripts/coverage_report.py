#!/usr/bin/env python3
"""CLI für die Coverage-Übersicht — was ist bereits gescrapt? (read-only)

Ground-truth-basiert (players ⨯ scrape_periods ⨯ game_results), drei
Dimensionen: Federation, Analysegruppe, ELO-Band — jeweils pro Jahr.

Usage:
    python scripts/coverage_report.py --dimension analysis-group
    python scripts/coverage_report.py --dimension federation --year-from 2020 --federation GER,AUT,SUI
    python scripts/coverage_report.py --dimension elo --band-width 100 --csv coverage.csv
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.coverage import (
    DEFAULT_YEAR_FROM,
    DEFAULT_YEAR_TO,
    coverage_by_analysis_group,
    coverage_by_elo_band,
    coverage_by_federation,
)
from orchestrator.setup_db import connect

logger = logging.getLogger(__name__)

DIMENSIONS = ("federation", "analysis-group", "elo")


def print_table(rows: list[dict], dim_key: str):
    if not rows:
        print("\n  Keine Daten im gewählten Bereich.\n")
        return
    print()
    print(f"  {'Dimension':<16} {'Jahr':<6} {'Aktiv':>7} {'Gescr.':>7} {'%Spl':>6} "
          f"{'Perioden':>16} {'%Per':>6} {'ok':>7} {'Partien':>9}")
    print("  " + "-" * 88)
    last_dim = None
    for r in rows:
        dim = str(r[dim_key])
        show = dim if dim != last_dim else ""
        last_dim = dim
        per = f"{r['periods_attempted']:,}/{r['periods_expected']:,}"
        print(f"  {show:<16} {r['year']:<6} {r['players_active']:>7,} "
              f"{r['players_scraped']:>7,} {r['pct_players']:>5.1f}% "
              f"{per:>16} {r['pct_periods']:>5.1f}% {r['periods_ok']:>7,} "
              f"{r['games']:>9,}")
    print()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dimension", choices=DIMENSIONS, required=True)
    parser.add_argument("--year-from", type=int, default=DEFAULT_YEAR_FROM)
    parser.add_argument("--year-to", type=int, default=DEFAULT_YEAR_TO)
    parser.add_argument("--band-width", type=int, default=100,
                        help="Bandbreite für --dimension elo (default 100)")
    parser.add_argument("--federation", metavar="FED[,FED...]",
                        help="Nur diese Föderationen anzeigen (Filter auf die Ausgabe)")
    parser.add_argument("--csv", metavar="FILE", help="Ergebnis als CSV exportieren")
    args = parser.parse_args()

    conn = connect()
    try:
        logger.info("Berechne Coverage (%s, %d–%d) ...",
                    args.dimension, args.year_from, args.year_to)
        if args.dimension == "federation":
            rows, dim_key = coverage_by_federation(
                conn, args.year_from, args.year_to), "federation"
        elif args.dimension == "analysis-group":
            rows, dim_key = coverage_by_analysis_group(
                conn, args.year_from, args.year_to), "analysis_group"
        else:
            rows, dim_key = coverage_by_elo_band(
                conn, args.band_width, args.year_from, args.year_to), "elo_band"
    finally:
        conn.close()

    if args.federation and dim_key == "federation":
        feds = set(args.federation.split(","))
        rows = [r for r in rows if r["federation"] in feds]

    print_table(rows, dim_key)

    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"  {len(rows)} Zeilen exportiert nach {args.csv}")


if __name__ == "__main__":
    main()
