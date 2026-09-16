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

# Ursachen-Taxonomie für non-ok-Fenster (Präzedenz = Reihenfolge; erste Regel gewinnt).
# Quelle der Kategorien: docs/project_status.md §6.3.
CATEGORIES = {
    "struktur_2008":     "Fenster vor 2009: Quartalsfenster + global-Gruppen ohne Early-Scraping",
    "fehlende_perioden": "Monate im Fenster ohne scrape_periods-Eintrag (Scraping-Lücke/offen)",
    "spiegel_delta":     "Nachbarfenster mit entgegengesetztem Δadj, hebt sich im Paar auf",
    "korrektur_rest":    "Bekannte FIDE-Korrektur im Fenster, erklärt das Delta nur teilweise",
    "k40_verdacht":      "K=40-Monat im Fenster: Timing-Effekte schnell aufsteigender Spieler",
    "unerklaert":        "Keine bekannte Ursache — genauer untersuchen",
}

# ── SQL ───────────────────────────────────────────────────────────────────────

# For each player find all consecutive pairs of published_rating snapshots,
# then compute expected vs. scraped change for the window in between.
_QC_COMPUTE_SQL_TEMPLATE = """
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
    -- Sum games in periods (T1, T2]: the games in period T produced the
    -- published_rating for T, so the change from published[T1] to
    -- published[T2] equals SUM of games where T1 < period <= T2.
    SELECT
        gr.fide_id,
        p.period_start,
        p.period_end,
        COALESCE(SUM(gr.rating_change_weighted), 0) AS scraped_change
    FROM pairs p
    JOIN game_results gr
      ON  gr.fide_id = p.fide_id
      AND gr.period >  p.period_start
      AND gr.period <= p.period_end
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
            p.period_start + INTERVAL '1 month',
            p.period_end,
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
{year_filter}
"""


def _build_qc_sql(from_year: int | None = None, to_year: int | None = None) -> str:
    conditions = []
    if from_year:
        conditions.append(f"EXTRACT(YEAR FROM p.period_end) >= {from_year}")
    if to_year:
        conditions.append(f"EXTRACT(YEAR FROM p.period_end) <= {to_year}")
    year_filter = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return _QC_COMPUTE_SQL_TEMPLATE.format(year_filter=year_filter)

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


def run_qc(
    conn,
    warn: float,
    error: float,
    rebuild: bool,
    from_year: int | None = None,
    to_year: int | None = None,
) -> int:
    """Compute QC rows and upsert into qc_rating_check. Returns row count."""
    if rebuild:
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE qc_rating_check")
        logger.info("qc_rating_check truncated")

    sql = _build_qc_sql(from_year, to_year)
    logger.info("Computing QC windows (this may take a moment)...")
    with conn.cursor() as cur:
        cur.execute(sql)
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


# ── Klassifikation ───────────────────────────────────────────────────────────

_CLASSIFY_SQL = """
WITH adj AS (
    SELECT
        fide_id,
        period_start,
        period_end,
        (delta - correction) AS delta_adj,
        LAG(delta - correction)  OVER w AS prev_adj,
        LEAD(delta - correction) OVER w AS next_adj,
        LAG(period_end)          OVER w AS prev_end,
        LEAD(period_start)       OVER w AS next_start
    FROM qc_rating_check
    WINDOW w AS (PARTITION BY fide_id ORDER BY period_start)
)
UPDATE qc_rating_check q
SET category = CASE
    WHEN q.period_start < DATE '2009-01-01' THEN 'struktur_2008'
    WHEN q.missing_periods > 0              THEN 'fehlende_perioden'
    WHEN (a.next_start = q.period_end
          AND ABS(a.delta_adj) >= %(warn)s AND ABS(a.next_adj) >= %(warn)s
          AND ABS(a.delta_adj + a.next_adj) <= %(warn)s
          AND a.delta_adj * a.next_adj < 0)
      OR (a.prev_end = q.period_start
          AND ABS(a.delta_adj) >= %(warn)s AND ABS(a.prev_adj) >= %(warn)s
          AND ABS(a.delta_adj + a.prev_adj) <= %(warn)s
          AND a.delta_adj * a.prev_adj < 0) THEN 'spiegel_delta'
    WHEN q.correction <> 0                  THEN 'korrektur_rest'
    WHEN EXISTS (
        SELECT 1 FROM scrape_periods sp
        WHERE sp.fide_id = q.fide_id
          AND sp.period >  q.period_start
          AND sp.period <= q.period_end
          AND sp.k_factor = 40
    )                                       THEN 'k40_verdacht'
    ELSE 'unerklaert'
END
FROM adj a
WHERE a.fide_id      = q.fide_id
  AND a.period_start = q.period_start
  AND a.period_end   = q.period_end
  AND q.flag != 'ok'
"""

_CLASSIFY_RESET_SQL = """
UPDATE qc_rating_check SET category = NULL
WHERE flag = 'ok' AND category IS NOT NULL
"""


def classify(conn, warn: float) -> int:
    """Klassifiziert alle non-ok-Fenster nach Ursache (Spalte category).

    Präzedenz siehe CATEGORIES. Idempotent; OK-Fenster werden auf NULL
    zurückgesetzt (relevant nach Re-Runs, wenn ein Fenster wieder ok wird).
    Returns: Anzahl klassifizierter Fenster.
    """
    with conn:
        with conn.cursor() as cur:
            cur.execute(_CLASSIFY_SQL, {"warn": warn})
            n = cur.rowcount
            cur.execute(_CLASSIFY_RESET_SQL)
    logger.info("  %d non-ok windows classified", n)
    return n


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


def export_csv(conn, path: str):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                q.fide_id, p.name, p.federation, p.analysis_group,
                q.period_start, q.period_end,
                q.published_start, q.published_end,
                q.expected_change, q.scraped_change, q.delta,
                q.correction, q.delta - q.correction AS delta_adj,
                q.missing_periods, q.flag, q.category
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

    conn = psycopg2.connect(
        get_database_url(),
        options="-c statement_timeout=1800000",  # 30 min; QC cross-join can be slow
    )
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
