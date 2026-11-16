-- owner: analysis (Grenzen der Leserolle; Review F-24, Paket 5d)
-- Migration 030: Leserolle begrenzen
--
-- Als Superuser einspielen (wie 022):
--   docker exec -i fide-db psql -U postgres -d fidedb < migrations/030_reader_limits.sql
--
-- 1. Temporäre Tabellen: PostgreSQL erlaubt sie standardmäßig jedem, der sich
--    verbinden darf (TEMPORARY an PUBLIC). Die Leserolle des Frontends braucht
--    das nicht; eine entgleiste Sitzung könnte so Plattenplatz belegen.
--    fide_ingest behält das Recht (resolve_opponents.py arbeitet mit einer
--    temporären Tabelle), der Eigentümer "fide" hat es ohnehin.
-- 2. Zeitgrenze: Abfragen von fide_reader enden nach 60 s. Die Seiten-Abfragen
--    des Frontends brauchen unter 4 s; ARPAD setzt sich eine eigene, engere
--    Grenze. Notebooks heben die Grenze für ihre Sitzung auf
--    (core.db.connect_analysis) — sie ist ein Schutz vor Ausreißern, keine
--    Sicherheitsgrenze: jede Sitzung kann ihren eigenen Wert ändern.
--
-- Gilt nur für diese Datenbank (IN DATABASE), idempotent, läuft auch auf einer
-- frischen PG ohne die Rollen fehlerfrei durch.

DO $$
BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fide_ingest') THEN
        EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO fide_ingest', current_database());
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fide_reader') THEN
        EXECUTE format('ALTER ROLE fide_reader IN DATABASE %I SET statement_timeout = %L',
                       current_database(), '60s');
    END IF;
END
$$;
