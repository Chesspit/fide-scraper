-- Migration 013: Orchestrator-Queue von SQLite (scraper.db) nach PostgreSQL.
-- Review #5 (Architektur-Review 2026-07-03): eine Datenbank für alles —
-- Queue-State (scrape_groups/scrape_runs) zieht ins Schema "orchestrator"
-- der fidedb um. Damit entfallen scraper.db, das SQLite-Backup und die
-- Pi-Export/Merge-Skripte (Geräte sprechen direkt mit PG).
--
-- Spaltentypen spiegeln orchestrator/setup_db.py (vor der Migration):
-- Zeitstempel waren dort ISO-Strings in Container-Localtime (UTC) — hier
-- TIMESTAMP ohne Zeitzone, Vergleiche laufen über localtimestamp.
-- update_only bleibt INTEGER (0/1) wie in SQLite, damit Group-Dataclass
-- und Worker-Logik unverändert bleiben.

CREATE SCHEMA IF NOT EXISTS orchestrator;

CREATE TABLE IF NOT EXISTS orchestrator.scrape_groups (
    id              SERIAL PRIMARY KEY,
    federation      TEXT    NOT NULL,
    continent       TEXT    NOT NULL,
    year            INTEGER NOT NULL,
    elo_min         INTEGER NOT NULL,
    elo_max         INTEGER NOT NULL,
    player_count    INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    priority        INTEGER NOT NULL,
    retries         INTEGER NOT NULL DEFAULT 0,
    last_run_at     TIMESTAMP,
    records_found   INTEGER,
    notes           TEXT,
    device          TEXT,             -- NULL = jedes Gerät
    profile         TEXT,             -- NULL = Fuzzy-Auswahl
    thread_affinity TEXT,             -- NULL = residential, 'dc_de'/... = DC-Thread
    update_only     INTEGER NOT NULL DEFAULT 0,  -- 1 = nur bereits gescrapte Spieler
    UNIQUE (federation, year, elo_min)
);

CREATE INDEX IF NOT EXISTS idx_groups_status_priority
    ON orchestrator.scrape_groups (status, priority);
CREATE INDEX IF NOT EXISTS idx_groups_fed_year_status
    ON orchestrator.scrape_groups (federation, year, status);
CREATE INDEX IF NOT EXISTS idx_groups_affinity
    ON orchestrator.scrape_groups (thread_affinity, status);

CREATE TABLE IF NOT EXISTS orchestrator.scrape_runs (
    id            SERIAL PRIMARY KEY,
    group_id      INTEGER NOT NULL REFERENCES orchestrator.scrape_groups (id),
    started_at    TIMESTAMP NOT NULL,
    finished_at   TIMESTAMP,
    status        TEXT,
    records_found INTEGER,
    error_msg     TEXT,
    proxy_used    TEXT,
    profile_used  TEXT,
    mb_downloaded DOUBLE PRECISION,
    thread_slot   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_runs_group
    ON orchestrator.scrape_runs (group_id);
