#!/usr/bin/env python3
"""Quality-control check: do scraped rating changes explain TXT-snapshot deltas?

For every player with at least two consecutive published_rating entries the script
computes:

    expected_change = published_rating[T2] - published_rating[T1]
    scraped_change  = SUM(rating_change_weighted) for periods T1 <= period < T2
    delta           = expected_change - scraped_change   (0 = perfect)

Results are written to qc_rating_check and a summary is printed.

Flags:
    ok    |delta| <= WARN_THRESHOLD  (default 5)
    warn  |delta| <= ERROR_THRESHOLD (default 15)
    error |delta| >  ERROR_THRESHOLD

Usage:
    python quality_check.py                   # run + report
    python quality_check.py --rebuild         # truncate + full rebuild
    python quality_check.py --report-only     # print report from existing data
    python quality_check.py --csv out.csv     # export warn+error rows to CSV
    python quality_check.py --warn 10 --error 25
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.config import get_database_url

logger = logging.getLogger(__name__)

WARN_THRESHOLD  = 5
ERROR_THRESHOLD = 15

# ── SQL ───────────────────────────────────────────────────────────────────────

# For each player find all consecutive pairs of published_rating snapshots,
# then compute expected vs. scraped change for the window in between.
_QC_COMPUTE_SQL = """
WITH snapshots AS (
    SELECT
        fide_id,
        period                                                  AS snap_period,
        published_rating,
        LEAD(period)          OVER (PARTITION BY fide_id ORDER BY period) AS next_period,
        LEAD(published_rating) OVER (PARTITION BY fide_id ORDER BY period) AS next_rating
    FROM rating_history
    WHERE published_rating IS NOT NULL
      AND fide_id IN (SELECT DISTINCT fide_id FROM scrape_periods)
),
pairs AS (
    SELECT
        fide_id,
        snap_period           AS period_start,
        next_period           AS period_end,
        published_rating      AS published_start,
        next_rating           AS published_end,
        next_rating - published_rating AS expected_change
    FROM snapshots
    WHERE next_period IS NOT NULL
),
scraped AS (
    SELECT
        gr.fide_id,
        p.period_start,
        p.period_end,
        COALESCE(SUM(gr.rating_change_weighted), 0) AS scraped_change
    FROM pairs p
    JOIN game_results gr
      ON  gr.fide_id = p.fide_id
      AND gr.period >= p.period_start
      AND gr.period <  p.period_end
    GROUP BY gr.fide_id, p.period_start, p.period_end
),
missing AS (
    -- count months in window that have no scrape_periods entry
    SELECT
        p.fide_id,
        p.period_start,
        p.period_end,
        COUNT(*) AS missing_periods
    FROM pairs p
    CROSS JOIN LATERAL (
        SELECT generate_series(
            p.period_start,
            p.period_end - INTERVAL '1 month',
            INTERVAL '1 month'
        )::date AS m
    ) months
    WHERE NOT EXISTS (
        SELECT 1 FROM scrape_periods sp
        WHERE sp.fide_id = p.fide_id AND sp.period = months.m
    )
    GROUP BY p.fide_id, p.period_start, p.period_end
),
corrections AS (
    -- sum of known FIDE rating corrections whose period falls within the window.
    -- A correction at period T is embedded in published_rating[T], so it affects
    -- any window where period_start < T <= period_end.
    SELECT
        p.fide_id,
        p.period_start,
        p.period_end,
        COALESCE(SUM(rc.amount), 0) AS correction_sum
    FROM pairs p
    LEFT JOIN rating_corrections rc
        ON  rc.fide_id = p.fide_id
        AND rc.period  >  p.period_start
        AND rc.period  <= p.period_end
    GROUP BY p.fide_id, p.period_start, p.period_end
)
SELECT
    p.fide_id,
    p.period_start,
    p.period_end,
    p.published_start,
    p.published_end,
    p.expected_change,
    COALESCE(s.scraped_change, 0)                      AS scraped_change,
    p.expected_change - COALESCE(s.scraped_change, 0)  AS delta,
    COALESCE(m.missing_periods, 0)                     AS missing_periods,
    COALESCE(c.correction_sum, 0)                      AS correction
FROM pairs p
LEFT JOIN scraped     s USING (fide_id, period_start, period_end)
LEFT JOIN missing     m USING (fide_id, period_start, period_end)
LEFT JOIN corrections c USING (fide_id, period_start, period_end)
"""

_UPSERT_SQL = """
INSERT INTO qc_rating_check
    (fide_id, period_start, period_end, published_start, published_end,
     expected_change, scraped_change, delta, missing_periods, correction, flag, checked_at)
VALUES %s
ON CONFLICT (fide_id, period_start, period_end)
DO UPDATE SET
    published_start  = EXCLUDED.published_start,
    published_end    = EXCLUDED.published_end,
    expected_change  = EXCLUDED.expected_change,
    scraped_change   = EXCLUDED.scraped_change,
    delta            = EXCLUDED.delta,
    missing_periods  = EXCLUDED.missing_periods,
    correction       = EXCLUDED.correction,
    flag             = EXCLUDED.flag,
    checked_at       = EXCLUDED.checked_at
"""


# ── Core logic ────────────────────────────────────────────────────────────────

def _flag(delta: float, warn: float, error: float) -> str:
    abs_delta = abs(delta)
    if abs_delta <= warn:
        return "ok"
    if abs_delta <= error:
        return "warn"
    return "error"


def run_qc(conn, warn: float, error: float, rebuild: bool) -> int:
    """Compute QC rows and upsert into qc_rating_check. Returns row count."""
    if rebuild:
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE qc_rating_check")
        logger.info("qc_rating_check truncated")

    logger.info("Computing QC windows (this may take a moment)...")
    with conn.cursor() as cur:
        cur.execute(_QC_COMPUTE_SQL)
        rows = cur.fetchall()
    logger.info("  %d windows computed", len(rows))

    if not rows:
        return 0

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    values = [
        (
            fide_id,
            period_start,
            period_end,
            published_start,
            published_end,
            float(expected_change),
            float(scraped_change),
            float(delta),
            missing_periods,
            float(correction),
            _flag(float(delta) - float(correction), warn, error),
            now,
        )
        for (fide_id, period_start, period_end, published_start, published_end,
             expected_change, scraped_change, delta, missing_periods, correction) in rows
    ]

    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, _UPSERT_SQL, values, page_size=2000)

    logger.info("  %d rows upserted into qc_rating_check", len(values))
    return len(values)


# ── Report ────────────────────────────────────────────────────────────────────

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
                q.flag
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
              f"{'Exp':>5} {'Got':>7} {'Δ':>6} {'Corr':>5} {'Δadj':>6} {'Miss':>5} {'Flag':<6}")
        print("  " + "-" * 116)
        for (fide_id, name, group, swiss, t1, t2,
             pub_s, pub_e, exp, got, delta, corr, delta_adj, miss, flag) in bad:
            grp = group or ("swiss" if swiss else "-")
            print(f"  {fide_id:>8} {name:<28.27} {grp:<14} {str(t1):<10} {str(t2):<10} "
                  f"{exp:>+5.0f} {got:>+7.1f} {delta:>+6.1f} {corr:>+5.0f} {delta_adj:>+6.1f} "
                  f"{miss:>5} {flag:<6}")
    else:
        print("  No flagged windows — data looks clean.")
    print()


def export_csv(conn, path: str):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                q.fide_id, p.name, p.federation, p.analysis_group,
                q.period_start, q.period_end,
                q.published_start, q.published_end,
                q.expected_change, q.scraped_change, q.delta,
                q.correction, q.delta - q.correction AS delta_adj,
                q.missing_periods, q.flag
            FROM qc_rating_check q
            JOIN players p USING (fide_id)
            WHERE q.flag != 'ok'
            ORDER BY ABS(q.delta - q.correction) DESC
        """)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)

    print(f"  {len(rows)} flagged rows exported to {path}")


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
    parser.add_argument("--csv", metavar="FILE",
                        help="Export warn+error rows to CSV")
    parser.add_argument("--warn",  type=float, default=WARN_THRESHOLD,
                        help=f"Warn threshold in rating points (default {WARN_THRESHOLD})")
    parser.add_argument("--error", type=float, default=ERROR_THRESHOLD,
                        help=f"Error threshold in rating points (default {ERROR_THRESHOLD})")
    args = parser.parse_args()

    conn = psycopg2.connect(
        get_database_url(),
        options="-c statement_timeout=1800000",  # 30 min; QC cross-join can be slow
    )
    try:
        if not args.report_only:
            run_qc(conn, warn=args.warn, error=args.error, rebuild=args.rebuild)

        print_report(conn, warn=args.warn, error=args.error)

        if args.csv:
            export_csv(conn, args.csv)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
