#!/usr/bin/env python3
"""Reconciliation check: official monthly rating lists vs. scraped game data.

For every player the official published ratings (rating_history.published_rating,
imported from the monthly FIDE TXT lists) are compared against the sum of scraped
per-game rating changes (game_results.rating_change_weighted):

    expected  = published[T2] - published[T1]        (consecutive snapshots)
    scraped   = SUM(rating_change_weighted)  for T1 < period <= T2
    residual  = expected - scraped - known_corrections

Logik: qc/reconcile.py — dieses Skript ist nur die CLI.

A window whose residual is not explained by any rule indicates broken or
incomplete scraping.

Rule layer (extensible — add a named Rule class, the core stays untouched):
    KnownCorrectionRule   adjusts residuals by rating_corrections amounts
                          (e.g. the FIDE March-2024 one-off boost)
    ToleranceRule         |residual| <= 1 counts as rounding noise, explained
    RollingWindowRule     a tournament's effect may slip into the next official
                          list ("Monatsverschiebung"); consecutive windows whose
                          cumulative residual cancels out within a configurable
                          span (default 2 months, then 12) are explained together

Pre-2012-08 quarterly lists need no special casing: windows are built from
consecutive snapshots, so a quarterly window simply spans three game months.

Name matching between the official lists (players.name) and scraped game data
(game_results.opponent_name) is fuzzy (rapidfuzz) and never discards silently:
every name lands in a confidence bucket (exact / high / uncertain / unmatched)
and the uncertain ones are listed explicitly.

Usage:
    python reconcile_ratings.py run [--group female_top,male_control]
                                    [--tolerance 1] [--windows 2,12]
                                    [--csv out.csv]
    python reconcile_ratings.py verify-names [--sample 500] [--group ...]
"""

import argparse
import logging

from qc.reconcile import (
    BUCKET_EXACT,
    BUCKET_HIGH,
    BUCKET_UNCERTAIN,
    BUCKET_UNMATCHED,
    DEFAULT_TOLERANCE,
    DEFAULT_WINDOWS,
    MatchResult,
    NameMatcher,
    _group_filter,
    default_rules,
    export_csv,
    load_corrections,
    load_player_series,
    reconcile,
    summarize,
)

logger = logging.getLogger(__name__)


def print_report(summary: dict, names: dict[int, str] | None = None):
    total = summary["total"]
    unexplained = summary["unexplained"]
    explained = total - len(unexplained)
    names = names or {}

    print()
    print("=" * 64)
    print("  Reconciliation: offizielle Listen vs. gescrapte Partien")
    print("=" * 64)
    print(f"  Fenster gesamt   : {total:>7,}")
    if total:
        print(f"  Erklärt          : {explained:>7,}  ({100 * explained / total:.1f}%)")
        for rule, n in sorted(summary["by_rule"].items(), key=lambda kv: -kv[1]):
            print(f"    · {rule:<18}: {n:>7,}")
        print(f"  Unerklärt        : {len(unexplained):>7,}  "
              f"({100 * len(unexplained) / total:.1f}%)")
    print()

    if unexplained:
        print(f"  Top unerklärte Fenster (max. 20, nach |Residual|):")
        print(f"  {'FIDE-ID':>9} {'Name':<26} {'T1':<10} {'T2':<10} "
              f"{'Exp':>5} {'Scraped':>8} {'Adj':>5} {'Resid':>7} {'Miss':>5}")
        print("  " + "-" * 92)
        for w in unexplained[:20]:
            print(f"  {w.fide_id:>9} {names.get(w.fide_id, '-'):<26.25} "
                  f"{w.period_start} {w.period_end} "
                  f"{w.expected_change:>+5} {w.scraped_change:>+8.1f} "
                  f"{w.adjustment:>+5.0f} {w.residual:>+7.1f} {w.missing_periods:>5}")
    else:
        print("  Keine unerklärten Fenster — Scraping vollständig plausibel.")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _connect():
    from core.db import connect
    return connect(role="qc")


def cmd_run(args):
    groups = args.group.split(",") if args.group else None
    windows = tuple(int(x) for x in args.windows.split(","))

    conn = _connect()
    try:
        logger.info("Loading player series%s ...",
                    f" for groups {groups}" if groups else "")
        all_series = load_player_series(conn, groups)
        corrections = load_corrections(conn)
        logger.info("  %d players, %d known corrections",
                    len(all_series), len(corrections))

        rules = default_rules(corrections, args.tolerance, windows)
        for s in all_series:
            reconcile(s, rules)

        with conn.cursor() as cur:
            cur.execute("SELECT fide_id, name FROM players WHERE fide_id = ANY(%s)",
                        ([s.fide_id for s in all_series],))
            names = dict(cur.fetchall())
    finally:
        conn.close()

    summary = summarize(all_series)
    print_report(summary, names)
    if args.csv:
        export_csv(all_series, args.csv)


def cmd_verify_names(args):
    groups = args.group.split(",") if args.group else None
    gf, params = _group_filter(groups)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT DISTINCT gr.opponent_name
                FROM game_results gr
                JOIN players p USING (fide_id)
                WHERE gr.opponent_name IS NOT NULL {gf}
                LIMIT %s
            """, params + (args.sample,))
            scraped_names = [r[0] for r in cur.fetchall()]

            cur.execute("""
                SELECT name FROM players
                WHERE name IS NOT NULL AND std_rating IS NOT NULL
            """)
            official = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    logger.info("Matching %d scraped names against %d official entries ...",
                len(scraped_names), len(official))
    matcher = NameMatcher(official)
    results = matcher.match_all(scraped_names)

    buckets: dict[str, list[MatchResult]] = {}
    for r in results:
        buckets.setdefault(r.bucket, []).append(r)

    print()
    print("  Namensabgleich gescrapte Partien ↔ offizielle Liste")
    print("  " + "-" * 52)
    for bucket in (BUCKET_EXACT, BUCKET_HIGH, BUCKET_UNCERTAIN, BUCKET_UNMATCHED):
        n = len(buckets.get(bucket, []))
        print(f"  {bucket:<12}: {n:>6}  ({100 * n / max(len(results), 1):.1f}%)")
    print()
    for r in buckets.get(BUCKET_UNCERTAIN, [])[:20]:
        print(f"    ? '{r.query}' → '{r.matched}' (Score {r.score:.0f})")
    for r in buckets.get(BUCKET_UNMATCHED, [])[:20]:
        print(f"    ✗ '{r.query}' → kein Match")
    print()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Reconciliation über die historischen Daten")
    p_run.add_argument("--group", help="Kommagetrennte analysis_group-Werte")
    p_run.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                       help=f"Toleranz in Elo-Punkten (default {DEFAULT_TOLERANCE})")
    p_run.add_argument("--windows", default=",".join(str(w) for w in DEFAULT_WINDOWS),
                       help="Rollierende Fenster in Monaten (default 2,12)")
    p_run.add_argument("--csv", metavar="FILE", help="Alle Fenster als CSV exportieren")
    p_run.set_defaults(func=cmd_run)

    p_names = sub.add_parser("verify-names", help="Fuzzy-Namensabgleich prüfen")
    p_names.add_argument("--sample", type=int, default=500,
                         help="Anzahl gescrapte Namen (default 500)")
    p_names.add_argument("--group", help="Kommagetrennte analysis_group-Werte")
    p_names.set_defaults(func=cmd_verify_names)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
