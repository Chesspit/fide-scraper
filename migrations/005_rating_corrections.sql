-- Migration 005: rating_corrections
-- Stores known non-game rating adjustments (e.g. FIDE one-off corrections).
-- Joined by QC and analysis to exclude non-performance rating jumps.

CREATE TABLE IF NOT EXISTS rating_corrections (
    fide_id     INTEGER NOT NULL REFERENCES players(fide_id),
    period      DATE NOT NULL,              -- period in which correction was applied
    amount      INTEGER NOT NULL,           -- ELO points added (positive = boost)
    corr_type   TEXT NOT NULL DEFAULT 'fide_one_off',
    source      TEXT,                       -- 'snapshot_delta' | 'formula'
    PRIMARY KEY (fide_id, period, corr_type)
);

CREATE INDEX IF NOT EXISTS rating_corrections_period_idx ON rating_corrections (period);

-- ─────────────────────────────────────────────────────────────────────────────
-- FIDE March 2024 one-off correction for all players with rating < 2000
-- Formula: +0.4 × (2000 − post_game_rating), applied after game results.
-- Decided: December 2023  |  Effective: 2024-03-01 rating list
--
-- For scraped players: amount = published[2024-03] − published[2024-02] − game_change
-- For un-scraped players: amount ≈ ROUND(0.4 × (2000 − Ro))   [exact for inactive]
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO rating_corrections (fide_id, period, amount, corr_type, source)
SELECT
    feb.fide_id,
    '2024-03-01'::date,
    CASE
        WHEN sp.fide_id IS NOT NULL
        THEN ROUND(
                mar.published_rating
                - feb.published_rating
                - COALESCE(gc.game_change, 0)
             )::INTEGER
        ELSE ROUND(0.4 * (2000 - feb.published_rating))::INTEGER
    END,
    'fide_one_off',
    CASE WHEN sp.fide_id IS NOT NULL THEN 'snapshot_delta' ELSE 'formula' END

FROM rating_history feb
JOIN rating_history mar
    ON  mar.fide_id = feb.fide_id
    AND mar.period  = '2024-03-01'
LEFT JOIN scrape_periods sp
    ON  sp.fide_id = feb.fide_id
    AND sp.period  = '2024-03-01'
    AND sp.status  = 'ok'
LEFT JOIN (
    SELECT fide_id, SUM(rating_change_weighted) AS game_change
    FROM   game_results
    WHERE  period = '2024-03-01'
    GROUP  BY fide_id
) gc ON gc.fide_id = feb.fide_id

WHERE feb.period              = '2024-02-01'
  AND feb.published_rating    < 2000
  AND feb.published_rating    IS NOT NULL
  AND mar.published_rating    IS NOT NULL

ON CONFLICT DO NOTHING;
