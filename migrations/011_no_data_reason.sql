-- Migration 011: add no_data_reason to scrape_periods
--
-- Distinguishes three causes of status='no_data':
--   system_gap  — FIDE never published this period (pre-monthly era)
--   too_young   — player born < 10 years before period (not yet FIDE-rated)
--   inactive    — player existed and was rated, but didn't play

ALTER TABLE scrape_periods
    ADD COLUMN IF NOT EXISTS no_data_reason TEXT
    CHECK (no_data_reason IN ('system_gap', 'too_young', 'inactive'));

-- Backfill existing no_data rows
UPDATE scrape_periods sp
SET no_data_reason = CASE
    -- System gap: known FIDE non-publishing periods
    WHEN sp.period < '2008-04-01'
        THEN 'system_gap'
    WHEN EXTRACT(YEAR FROM sp.period) = 2008
     AND EXTRACT(MONTH FROM sp.period) NOT IN (4, 7, 10)
        THEN 'system_gap'
    WHEN EXTRACT(YEAR FROM sp.period) = 2009
     AND EXTRACT(MONTH FROM sp.period) NOT IN (1, 4, 7, 9, 11)
        THEN 'system_gap'
    WHEN sp.period >= '2010-01-01' AND sp.period < '2012-08-01'
     AND EXTRACT(MONTH FROM sp.period) NOT IN (1, 3, 5, 7, 9, 11)
        THEN 'system_gap'
    -- Too young: player born less than 10 years before period
    WHEN (SELECT p.birth_year FROM players p WHERE p.fide_id = sp.fide_id) IS NOT NULL
     AND EXTRACT(YEAR FROM sp.period)::int
         - (SELECT p.birth_year FROM players p WHERE p.fide_id = sp.fide_id) < 10
        THEN 'too_young'
    -- Default: genuine inactivity
    ELSE 'inactive'
END
WHERE sp.status = 'no_data';
