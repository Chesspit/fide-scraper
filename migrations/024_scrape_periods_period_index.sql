-- owner: qc
-- Migration 024: Index auf scrape_periods.period
--
-- Gleicher Grund wie 023: scrape_periods hatte nur den PK (fide_id, period), die
-- Datenprüfung scannte je Periode 24 Mio. Zeilen. CONCURRENTLY: siehe 023.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scrape_periods_period
    ON scrape_periods (period);
