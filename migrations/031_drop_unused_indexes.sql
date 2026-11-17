-- owner: ingest
-- migrate: no-transaction
-- Migration 031: fünf ungenutzte Indizes entfernen (Review F-09, Paket 6)
--
-- pg_stat_user_indexes zeigte für diese Indizes 0 Zugriffe, gemessen 19.10.–14.11.2026
-- (Review) und erneut 15.–17.11.2026 nach dem DB-Neustart. Zusammen 2,56 GB:
--   game_results_over_performance_idx  1'482 MB  (009)
--   game_results_opponent_sex_idx         376 MB  (007)
--   game_results_tournament_type_idx      320 MB  (007)
--   game_results_match_quality_idx        308 MB  (009)
--   idx_players_name_fed                   74 MB  (001)
-- Kein Code filtert selektiv auf diese Spalten: opponent_sex nur zusammen mit fide_id
-- (ARPAD, dafür reicht der Primärschlüssel) bzw. als IN ('M','F') (Notebook 14);
-- Namenssuchen laufen über ILIKE '%…%', dafür taugt ein B-Tree nicht.
-- Jeder Index musste trotzdem bei jedem Insert/Update von game_results mitgepflegt werden
-- (Anreicherung, Neuberechnung expected_score).
--
-- Rückweg: die CREATE INDEX-Zeilen aus 001/007/009 (mit CONCURRENTLY) erneut ausführen.
-- CONCURRENTLY: blockiert keine laufenden Reads/Writes, darf nicht in einer Transaktion
-- laufen; core/migrations.py führt die Befehle einzeln aus. IF EXISTS macht die Datei
-- wiederholbar, falls ein Lauf mittendrin abbricht.

DROP INDEX CONCURRENTLY IF EXISTS game_results_over_performance_idx;
DROP INDEX CONCURRENTLY IF EXISTS game_results_opponent_sex_idx;
DROP INDEX CONCURRENTLY IF EXISTS game_results_tournament_type_idx;
DROP INDEX CONCURRENTLY IF EXISTS game_results_match_quality_idx;
DROP INDEX CONCURRENTLY IF EXISTS idx_players_name_fed;
