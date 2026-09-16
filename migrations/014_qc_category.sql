-- Migration 014: Ursachen-Kategorie für QC-Fenster
--
-- category wird nur für flag != 'ok' gesetzt (OK-Fenster: NULL).
-- Taxonomie + Klassifikationsregeln: scripts/quality_check.py::classify()
-- Werte: struktur_2008 | fehlende_perioden | spiegel_delta |
--        korrektur_rest | k40_verdacht | unerklaert

ALTER TABLE qc_rating_check
    ADD COLUMN IF NOT EXISTS category TEXT;

CREATE INDEX IF NOT EXISTS qc_rating_check_category
    ON qc_rating_check (category)
    WHERE category IS NOT NULL;
