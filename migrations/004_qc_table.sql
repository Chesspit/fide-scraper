-- Quality-control table: validates that scraped rating changes explain
-- the delta between consecutive published_rating snapshots (TXT files).
-- Populated by scripts/quality_check.py; rebuilt after each new snapshot import.

CREATE TABLE IF NOT EXISTS qc_rating_check (
    fide_id          INTEGER NOT NULL REFERENCES players(fide_id),
    period_start     DATE    NOT NULL,  -- published_rating snapshot T1
    period_end       DATE    NOT NULL,  -- published_rating snapshot T2
    published_start  INTEGER NOT NULL,
    published_end    INTEGER NOT NULL,
    expected_change  NUMERIC(7,2) NOT NULL,  -- published_end - published_start
    scraped_change   NUMERIC(7,2) NOT NULL,  -- SUM(rating_change_weighted) in window
    delta            NUMERIC(7,2) NOT NULL,  -- expected - scraped; 0 = perfect
    missing_periods  INTEGER NOT NULL DEFAULT 0,  -- months without scrape_periods entry
    flag             TEXT    NOT NULL DEFAULT 'ok',  -- 'ok' | 'warn' | 'error'
    checked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (fide_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS qc_rating_check_flag ON qc_rating_check (flag)
    WHERE flag != 'ok';
CREATE INDEX IF NOT EXISTS qc_rating_check_period_start ON qc_rating_check (period_start);
