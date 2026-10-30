"""QC-Fenster: erklären die gescrapten Rating-Änderungen die Listen-Deltas?

Für jeden Spieler mit mindestens zwei aufeinanderfolgenden published_rating-
Einträgen:

    expected_change = published_rating[T2] - published_rating[T1]
    scraped_change  = SUM(rating_change_weighted) for periods T1 <= period < T2
    delta           = expected_change - scraped_change   (0 = perfect)

Ergebnisse landen in qc_rating_check (eigene QC-Tabelle), danach wird jedes
non-ok-Fenster einer Ursachen-Kategorie zugeordnet (CATEGORIES).

Bibliothek; CLI: scripts/quality_check.py. Vorher komplett im Skript.
"""

import csv
import logging

import psycopg2
import psycopg2.extras

from core.db import connect as _core_connect

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

def connect(statement_timeout_ms: int = 1_800_000):
    """Verbindung für QC-Läufe (Rolle qc); 30 min, der Cross-Join kann dauern."""
    return _core_connect(role="qc", statement_timeout_ms=statement_timeout_ms)


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
