-- Migration 006: add correction column to qc_rating_check
-- Stores the sum of known FIDE corrections (from rating_corrections) within each QC window.
-- The adjusted delta is delta - correction; flag is based on delta_adj.

ALTER TABLE qc_rating_check
    ADD COLUMN IF NOT EXISTS correction NUMERIC(7,2) NOT NULL DEFAULT 0;
