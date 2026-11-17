-- owner: ingest
-- Migration 032: Autovacuum-Schwellen für die drei grossen Tabellen (Paket 6)
--
-- Standard: Autovacuum/Analyze erst nach 20 % bzw. 10 % geänderten Zeilen, bei
-- rating_history (61 Mio Zeilen) also nach 12 Mio. Gemessen 17.11.2026: seit dem
-- DB-Neustart 15.11. kein einziger Vacuum/Analyze auf game_results, rating_history,
-- scrape_periods; rating_history nur zu 56 % «all-visible», game_results 78 %.
-- Folge: Index-Only-Scans müssen in den Heap (/c, Schritt 1: 2'867 Heap-Zugriffe für
-- 8'030 Zeilen, 4,6 s) und der Planer schätzt mit veralteter Statistik (143'979 statt
-- 8'030 Zeilen).
--
-- Neu: Vacuum nach 1 % geänderten bzw. eingefügten Zeilen, Analyze nach 0,5 %.
-- Bei rating_history ≈ 610'000 Zeilen, bei game_results ≈ 210'000 – der Monatsimport
-- und ein Scrape-Tag lösen damit je einen Lauf aus.
-- ALTER TABLE … SET (…) nimmt nur eine SHARE UPDATE EXCLUSIVE-Sperre: Lesen und
-- Schreiben laufen weiter. Rückweg: ALTER TABLE … RESET (…) mit denselben Namen.
--
-- Der einmalige Nachhol-Lauf (VACUUM (ANALYZE) je Tabelle) ist bewusst keine Migration:
-- er dauert je nach Tabelle Minuten bis eine Stunde und gehört in ein Deploy-Fenster.

ALTER TABLE game_results SET (
    autovacuum_vacuum_scale_factor        = 0.01,
    autovacuum_vacuum_insert_scale_factor = 0.01,
    autovacuum_analyze_scale_factor       = 0.005
);

ALTER TABLE rating_history SET (
    autovacuum_vacuum_scale_factor        = 0.01,
    autovacuum_vacuum_insert_scale_factor = 0.01,
    autovacuum_analyze_scale_factor       = 0.005
);

ALTER TABLE scrape_periods SET (
    autovacuum_vacuum_scale_factor        = 0.01,
    autovacuum_vacuum_insert_scale_factor = 0.01,
    autovacuum_analyze_scale_factor       = 0.005
);
