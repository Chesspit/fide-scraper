-- owner: qc
-- Migration 021: gespeicherte Läufe der Datenprüfung (qc/audit.py)
--
-- Ein Audit-Lauf dauert zu lange, um ihn im Dashboard live zu rechnen
-- (2026-01…04: 19 min). scripts/audit_data.py --store schreibt jeden Lauf
-- hierher, der QC-Bereich im Orchestrator-Dashboard liest nur über
-- qc/audit_store.py. Struktur der JSONB-Spalten: qc/audit_store.py::result_to_record.
--
-- QC schreibt nur in eigene Tabellen (qc_*), nie in Rohdaten.

CREATE TABLE IF NOT EXISTS qc_audit_runs (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_s      NUMERIC(10, 1),
    source          TEXT        NOT NULL DEFAULT 'manual',  -- manual | monthly_update
    since_period    DATE        NOT NULL,
    until_period    DATE,                                   -- NULL = bis Vormonat
    last_period     DATE,                                   -- letzte geprüfte Periode
    federations     TEXT[],                                 -- NULL = alle
    layers          INTEGER[]   NOT NULL,
    params          JSONB       NOT NULL DEFAULT '{}',      -- Schwellen, Stichprobengröße
    has_hard        BOOLEAN     NOT NULL,
    headline        JSONB       NOT NULL,                   -- Kopfzahlen (AuditResult.headline)
    checks          JSONB       NOT NULL,                   -- Liste je Check inkl. Stichprobe
    by_year         JSONB       NOT NULL DEFAULT '{}',
    by_federation   JSONB       NOT NULL DEFAULT '{}',
    skipped_periods DATE[]      NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_qc_audit_runs_finished
    ON qc_audit_runs (finished_at DESC);

COMMENT ON TABLE qc_audit_runs IS
    'Gespeicherte Läufe von scripts/audit_data.py --store; gelesen über qc/audit_store.py.';
