-- 019: only_period für Perioden-Reparatur-Gruppen (2026-09-17)
--
-- Eine Gruppe mit gesetztem only_period scrapt nur diese eine Periode und nur
-- Spieler, die laut Liste Partien hatten, aber keinen scrape_periods-Eintrag
-- dafür haben (worker.py::get_fide_ids). Anlass: vertauschte Liste 2024-06,
-- 22.830 Kombos vom Pre-Filter ohne Abruf als no_data markiert.
--
-- setup_db.ensure_schema() spiegelt dieses DDL idempotent — auf dem VPS wird
-- die Spalte beim ersten Worker-/Dashboard-Start mit neuem Code automatisch
-- angelegt; dieses Skript dokumentiert die Änderung im Migrationspfad.

ALTER TABLE orchestrator.scrape_groups ADD COLUMN IF NOT EXISTS only_period DATE;
