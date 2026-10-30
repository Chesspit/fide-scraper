#!/usr/bin/env python3
"""Quality-control check: do scraped rating changes explain TXT-snapshot deltas?

For every player with at least two consecutive published_rating entries the script
computes:

    expected_change = published_rating[T2] - published_rating[T1]
    scraped_change  = SUM(rating_change_weighted) for periods T1 <= period < T2
    delta           = expected_change - scraped_change   (0 = perfect)

Results are written to qc_rating_check and a summary is printed.
Logik: qc/rating_check.py — dieses Skript ist nur die CLI.

Flags:
    ok    |delta| <= WARN_THRESHOLD  (default 5)
    warn  |delta| <= ERROR_THRESHOLD (default 15)
    error |delta| >  ERROR_THRESHOLD

After computation every non-ok window is classified into a cause category
(column qc_rating_check.category, see CATEGORIES below).

Usage:
    python quality_check.py                   # run + classify + report
    python quality_check.py --rebuild         # truncate + full rebuild
    python quality_check.py --report-only     # print report from existing data
    python quality_check.py --classify-only   # re-classify existing windows only
    python quality_check.py --csv out.csv     # export warn+error rows to CSV
    python quality_check.py --warn 10 --error 25
"""

import argparse
import logging

from qc.rating_check import (
    CATEGORIES,
    ERROR_THRESHOLD,
    WARN_THRESHOLD,
    classify,
    connect,
    export_csv,
    run_qc,
)

logger = logging.getLogger(__name__)


def print_report(conn, warn: float, error: float):
    with conn.cursor() as cur:
        # Overall summary
        cur.execute("""
            SELECT
                COUNT(*)                                               AS total,
                SUM(CASE WHEN flag = 'ok'    THEN 1 ELSE 0 END)       AS ok,
                SUM(CASE WHEN flag = 'warn'  THEN 1 ELSE 0 END)       AS warn,
                SUM(CASE WHEN flag = 'error' THEN 1 ELSE 0 END)       AS error,
                ROUND(AVG(ABS(delta))::numeric, 1)                    AS avg_abs_delta,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(delta)) AS median_abs_delta,
                MAX(ABS(delta))                                        AS max_abs_delta
            FROM qc_rating_check
        """)
        row = cur.fetchone()
        total, ok, warn_n, error_n, avg_d, med_d, max_d = row

    print()
    print("=" * 60)
    print("  FIDE Scraper — Rating QC Report")
    print("=" * 60)
    print(f"  Windows checked  : {total:>7,}")
    print(f"  OK  (|Δ| ≤ {warn:.0f})   : {ok:>7,}  ({100*ok/total:.1f}%)")
    print(f"  Warn(|Δ| ≤ {error:.0f})  : {warn_n:>7,}  ({100*warn_n/total:.1f}%)")
    print(f"  Error(|Δ| > {error:.0f}) : {error_n:>7,}  ({100*error_n/total:.1f}%)")
    print(f"  Avg |Δ|          : {avg_d:>7}")
    print(f"  Median |Δ|       : {med_d:>7.1f}")
    print(f"  Max |Δ|          : {max_d:>7.1f}")
    print()

    # By year
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                EXTRACT(YEAR FROM period_start)::int  AS yr,
                COUNT(*)                              AS n,
                SUM(CASE WHEN flag='ok'    THEN 1 ELSE 0 END) AS ok,
                SUM(CASE WHEN flag='warn'  THEN 1 ELSE 0 END) AS warn,
                SUM(CASE WHEN flag='error' THEN 1 ELSE 0 END) AS error,
                ROUND(AVG(ABS(delta))::numeric, 1)            AS avg_d,
                SUM(CASE WHEN missing_periods > 0 THEN 1 ELSE 0 END) AS has_missing
            FROM qc_rating_check
            GROUP BY yr
            ORDER BY yr
        """)
        year_rows = cur.fetchall()

    print(f"  {'Jahr':<6} {'Windows':>7} {'OK%':>6} {'Warn':>5} {'Err':>5} {'Avg|Δ|':>7} {'MissingP':>9}")
    print("  " + "-" * 52)
    for yr, n, ok_n, warn_n, err_n, avg_d, missing in year_rows:
        print(f"  {yr:<6} {n:>7,} {100*ok_n/n:>5.1f}% {warn_n:>5} {err_n:>5} {avg_d:>7} {missing:>9}")
    print()

    # Kategorien nach Jahr (non-ok-Fenster)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                EXTRACT(YEAR FROM period_end)::int AS yr,
                category,
                COUNT(*) AS n
            FROM qc_rating_check
            WHERE flag != 'ok' AND category IS NOT NULL
            GROUP BY yr, category
            ORDER BY yr
        """)
        cat_rows = cur.fetchall()

    if cat_rows:
        cats = list(CATEGORIES)
        by_year: dict[int, dict[str, int]] = {}
        for yr, cat, n in cat_rows:
            by_year.setdefault(yr, {})[cat] = n
        print("  Ursachen-Kategorien (non-ok-Fenster) nach Jahr:")
        header = "  " + f"{'Jahr':<6}" + "".join(f"{c[:14]:>16}" for c in cats)
        print(header)
        print("  " + "-" * (6 + 16 * len(cats)))
        for yr in sorted(by_year):
            row = by_year[yr]
            print("  " + f"{yr:<6}" + "".join(f"{row.get(c, 0):>16,}" for c in cats))
        print()

    # Annual checksum (2013+): Dec[Y-1] + Σ games + corrections = Dec[Y]
    with conn.cursor() as cur:
        cur.execute("""
            WITH annual AS (
                SELECT
                    h1.fide_id,
                    EXTRACT(YEAR FROM h2.period)::int AS jahr,
                    ROUND((h1.published_rating
                           + COALESCE(gc.game_sum, 0)
                           + COALESCE(rc.corr_sum, 0)
                           - h2.published_rating)::numeric, 1) AS annual_diff
                FROM rating_history h1
                JOIN rating_history h2
                    ON  h2.fide_id = h1.fide_id
                    AND EXTRACT(MONTH FROM h1.period) = 12
                    AND EXTRACT(MONTH FROM h2.period) = 12
                    AND EXTRACT(YEAR FROM h2.period) = EXTRACT(YEAR FROM h1.period) + 1
                JOIN (SELECT DISTINCT fide_id FROM scrape_periods) sp
                    ON sp.fide_id = h1.fide_id
                LEFT JOIN (
                    SELECT fide_id, EXTRACT(YEAR FROM period)::int AS jahr,
                           SUM(rating_change_weighted) AS game_sum
                    FROM game_results
                    GROUP BY fide_id, EXTRACT(YEAR FROM period)::int
                ) gc ON gc.fide_id = h1.fide_id AND gc.jahr = EXTRACT(YEAR FROM h2.period)
                LEFT JOIN (
                    SELECT fide_id, EXTRACT(YEAR FROM period)::int AS jahr,
                           SUM(amount) AS corr_sum
                    FROM rating_corrections
                    GROUP BY fide_id, EXTRACT(YEAR FROM period)::int
                ) rc ON rc.fide_id = h1.fide_id AND rc.jahr = EXTRACT(YEAR FROM h2.period)
                WHERE EXTRACT(YEAR FROM h2.period) >= 2013
                  AND h1.published_rating IS NOT NULL
                  AND h2.published_rating IS NOT NULL
            ),
            annual_agg AS (
                SELECT
                    jahr,
                    COUNT(*)                                                          AS j_spieler,
                    ROUND(100.0 * COUNT(*) FILTER (WHERE ABS(annual_diff) <= 3)
                          / NULLIF(COUNT(*), 0), 1)                                  AS j_ok_pct,
                    COUNT(*) FILTER (WHERE ABS(annual_diff) > 3
                                      AND ABS(annual_diff) <= 10)                    AS j_warn,
                    COUNT(*) FILTER (WHERE ABS(annual_diff) > 10)                    AS j_error
                FROM annual
                GROUP BY jahr
            ),
            monthly_agg AS (
                SELECT
                    EXTRACT(YEAR FROM period_end)::int                               AS jahr,
                    COUNT(*)                                                          AS m_fenster,
                    ROUND(100.0 * COUNT(*) FILTER (WHERE flag='ok')
                          / NULLIF(COUNT(*), 0), 1)                                  AS m_ok_pct,
                    COUNT(*) FILTER (WHERE flag='warn')                              AS m_warn,
                    COUNT(*) FILTER (WHERE flag='error')                             AS m_error
                FROM qc_rating_check
                WHERE EXTRACT(YEAR FROM period_end) >= 2013
                GROUP BY EXTRACT(YEAR FROM period_end)
            )
            SELECT m.jahr,
                   m.m_fenster, m.m_ok_pct, m.m_warn, m.m_error,
                   a.j_spieler, a.j_ok_pct, a.j_warn, a.j_error
            FROM monthly_agg m
            JOIN annual_agg  a USING (jahr)
            ORDER BY m.jahr
        """)
        annual_rows = cur.fetchall()

    if annual_rows:
        print(f"  Jahres-Prüfsumme Dez[Y-1]→Dez[Y] (nur gescrapte Spieler, |Δ|≤3 = OK):")
        print(f"  {'Jahr':<6} {'M-Fenster':>10} {'M-OK%':>6} {'M-Warn':>7} {'M-Err':>6}"
              f"  {'J-Spieler':>10} {'J-OK%':>6} {'J-Warn':>7} {'J-Err':>6}")
        print("  " + "-" * 72)
        for (yr, m_fen, m_ok, m_warn, m_err,
             j_sp, j_ok, j_warn, j_err) in annual_rows:
            print(f"  {yr:<6} {m_fen:>10,} {m_ok:>5.1f}% {m_warn:>7} {m_err:>6}"
                  f"  {j_sp:>10,} {j_ok:>5.1f}% {j_warn:>7} {j_err:>6}")
        print()

    # Worst offenders (top 20 by |delta_adj|)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                q.fide_id,
                p.name,
                p.analysis_group,
                CASE WHEN p.swiss_2026 THEN 'Y' ELSE '' END AS swiss,
                q.period_start::date,
                q.period_end::date,
                q.published_start,
                q.published_end,
                q.expected_change,
                q.scraped_change,
                q.delta,
                q.correction,
                q.delta - q.correction          AS delta_adj,
                q.missing_periods,
                q.flag,
                q.category
            FROM qc_rating_check q
            JOIN players p USING (fide_id)
            WHERE q.flag != 'ok'
            ORDER BY ABS(q.delta - q.correction) DESC
            LIMIT 20
        """)
        bad = cur.fetchall()

    if bad:
        print(f"  Top flagged windows (worst {len(bad)}, ordered by |Δ_adj|):")
        print(f"  {'FIDE-ID':>8} {'Name':<28} {'Gruppe':<14} {'T1':<10} {'T2':<10} "
              f"{'Exp':>5} {'Got':>7} {'Δ':>6} {'Corr':>5} {'Δadj':>6} {'Miss':>5} {'Flag':<6} {'Kategorie':<18}")
        print("  " + "-" * 135)
        for (fide_id, name, group, swiss, t1, t2,
             pub_s, pub_e, exp, got, delta, corr, delta_adj, miss, flag, cat) in bad:
            grp = group or ("swiss" if swiss else "-")
            print(f"  {fide_id:>8} {name:<28.27} {grp:<14} {str(t1):<10} {str(t2):<10} "
                  f"{exp:>+5.0f} {got:>+7.1f} {delta:>+6.1f} {corr:>+5.0f} {delta_adj:>+6.1f} "
                  f"{miss:>5} {flag:<6} {cat or '-':<18}")
    else:
        print("  No flagged windows — data looks clean.")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true",
                        help="Truncate qc_rating_check before computing")
    parser.add_argument("--report-only", action="store_true",
                        help="Print report from existing data, skip computation")
    parser.add_argument("--classify-only", action="store_true",
                        help="Re-classify existing windows (category), skip computation")
    parser.add_argument("--csv", metavar="FILE",
                        help="Export warn+error rows to CSV")
    parser.add_argument("--warn",  type=float, default=WARN_THRESHOLD,
                        help=f"Warn threshold in rating points (default {WARN_THRESHOLD})")
    parser.add_argument("--error", type=float, default=ERROR_THRESHOLD,
                        help=f"Error threshold in rating points (default {ERROR_THRESHOLD})")
    parser.add_argument("--from-year", type=int, metavar="YYYY",
                        help="Only compute windows with period_end >= this year")
    parser.add_argument("--to-year",   type=int, metavar="YYYY",
                        help="Only compute windows with period_end <= this year")
    args = parser.parse_args()

    conn = connect()
    try:
        if not args.report_only and not args.classify_only:
            run_qc(conn, warn=args.warn, error=args.error, rebuild=args.rebuild,
                   from_year=args.from_year, to_year=args.to_year)

        if not args.report_only:
            classify(conn, warn=args.warn)

        print_report(conn, warn=args.warn, error=args.error)

        if args.csv:
            export_csv(conn, args.csv)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
