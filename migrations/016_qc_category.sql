-- Migration 016: Ursachen-Kategorie für QC-Fenster
-- (16.09.2026 von 014 auf 016 umnummeriert: die Nummer 014 war bereits durch
--  014_claimed_by.sql belegt. Inhaltlich unverändert, beide waren zu dem
--  Zeitpunkt längst angewendet.)
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
