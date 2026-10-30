-- owner: ingest/qc/analysis (Rechte für alle Blöcke)
-- Migration 022: DB-Rollen je Block (Phase 2a, beschlossen 18.10.2026)
--
-- ⚠️ VORBEREITET, NICHT ANGEWENDET. Anwenden nur im Deploy-Fenster, als
-- Superuser (CREATE ROLE braucht CREATEROLE):
--   docker exec -i fide-db psql -U postgres -d fidedb < migrations/022_db_roles.sql
-- Danach Passwörter setzen und Dienste einzeln umstellen: docs/db_rollen.md.
--
--   fide_ingest  Block 1: Rohdaten + orchestrator.* schreiben (Worker, Dashboard,
--                Monatslauf-Import, resolve_opponents)
--   fide_qc      Block 4: alles lesen, nur qc_* schreiben (audit_data --store,
--                quality_check)
--   fide_reader  Block 2/3: nur lesen (beide Frontend-Editionen, Notebooks)
--
-- Rollen entstehen NOLOGIN und ohne Passwort; Login wird erst mit
-- ALTER ROLE … LOGIN PASSWORD freigeschaltet. Idempotent, läuft auch auf einer
-- frischen PG ohne Rolle "fide" fehlerfrei durch (tests/test_db.py spielt alle
-- Migrationen ein). Eigentümer der Tabellen bleibt "fide" (Migrationen).

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fide_ingest') THEN
        CREATE ROLE fide_ingest NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fide_qc') THEN
        CREATE ROLE fide_qc NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fide_reader') THEN
        CREATE ROLE fide_reader NOLOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO fide_ingest, fide_qc, fide_reader',
                   current_database());
END
$$;

-- ── Lesen: alle drei ───────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA public TO fide_ingest, fide_qc, fide_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO fide_ingest, fide_qc, fide_reader;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO fide_ingest, fide_qc, fide_reader;

-- ── Block 1: Rohdaten schreiben ────────────────────────────────────────────
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO fide_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO fide_ingest;
-- QC-Ergebnisse gehören QC — Block 1 liest sie nur
REVOKE INSERT, UPDATE, DELETE ON qc_rating_check, qc_audit_runs FROM fide_ingest;

-- Orchestrator-Queue: Block 1 (Worker + Dashboard) schreibt, QC liest
-- (qc/integrity.py prüft scrape_groups)
GRANT USAGE ON SCHEMA orchestrator TO fide_ingest, fide_qc;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA orchestrator TO fide_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA orchestrator TO fide_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA orchestrator TO fide_qc;

-- ── Block 4: nur eigene Ergebnistabellen schreiben ─────────────────────────
-- TRUNCATE: quality_check.py --rebuild
GRANT INSERT, UPDATE, DELETE, TRUNCATE ON qc_rating_check, qc_audit_runs TO fide_qc;
GRANT USAGE, SELECT ON SEQUENCE qc_audit_runs_id_seq TO fide_qc;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'qc_rating_check_id_seq') THEN
        GRANT USAGE, SELECT ON SEQUENCE qc_rating_check_id_seq TO fide_qc;
    END IF;
END
$$;

-- ── Künftige Tabellen (von "fide" per Migration angelegt) ──────────────────
-- Neue qc_*-Tabellen brauchen weiterhin eigene GRANTs an fide_qc in ihrer
-- Migration; hier wird nur das Lesen automatisch nachgezogen.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fide') THEN
        ALTER DEFAULT PRIVILEGES FOR ROLE fide IN SCHEMA public
            GRANT SELECT ON TABLES TO fide_ingest, fide_qc, fide_reader;
        ALTER DEFAULT PRIVILEGES FOR ROLE fide IN SCHEMA public
            GRANT EXECUTE ON FUNCTIONS TO fide_ingest, fide_qc, fide_reader;
        ALTER DEFAULT PRIVILEGES FOR ROLE fide IN SCHEMA orchestrator
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fide_ingest;
        ALTER DEFAULT PRIVILEGES FOR ROLE fide IN SCHEMA orchestrator
            GRANT SELECT ON TABLES TO fide_qc;
    END IF;
END
$$;
