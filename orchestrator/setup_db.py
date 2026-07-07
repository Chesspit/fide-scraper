"""PostgreSQL-Verbindung + Schema der Orchestrator-Queue (Review #5).

Ersetzt die frühere SQLite-Datei /data/scraper.db: scrape_groups und
scrape_runs liegen jetzt im Schema "orchestrator" der fidedb (Migration
migrations/013_orchestrator_queue.sql — hier idempotent gespiegelt, damit
Test- und Erstinstallationen sich selbst provisionieren).

connect() liefert eine Autocommit-Verbindung mit search_path=orchestrator,
public — alle Queries in store.py/queue_manager.py bleiben dadurch
unqualifiziert. Retry-Logik analog scraper/db.py::ensure_connection():
Tunnel-Drops (Mac Mini/Pi) und PG-Neustarts dürfen den Worker nicht töten.
"""

import logging
import time

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

SCHEMA = "orchestrator"

# Verbindungs-Retry: 10 Versuche, Backoff 1→60 s gedeckelt (~5 Min gesamt) —
# gleiche Größenordnung wie scraper/db.py::ensure_connection().
CONNECT_RETRIES = 10
CONNECT_BACKOFF_CAP = 60.0

_SCHEMA_SQL = f"""
    CREATE SCHEMA IF NOT EXISTS {SCHEMA};

    CREATE TABLE IF NOT EXISTS {SCHEMA}.scrape_groups (
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
        device          TEXT,
        profile         TEXT,
        thread_affinity TEXT,
        update_only     INTEGER NOT NULL DEFAULT 0,
        claimed_by      TEXT,
        UNIQUE (federation, year, elo_min)
    );

    -- Phase B (Multi-Device): nachrüstbar auf Bestandsinstallationen,
    -- CREATE TABLE IF NOT EXISTS ändert existierende Tabellen nicht.
    ALTER TABLE {SCHEMA}.scrape_groups ADD COLUMN IF NOT EXISTS claimed_by TEXT;

    CREATE INDEX IF NOT EXISTS idx_groups_status_priority
        ON {SCHEMA}.scrape_groups (status, priority);
    CREATE INDEX IF NOT EXISTS idx_groups_fed_year_status
        ON {SCHEMA}.scrape_groups (federation, year, status);
    CREATE INDEX IF NOT EXISTS idx_groups_affinity
        ON {SCHEMA}.scrape_groups (thread_affinity, status);

    CREATE TABLE IF NOT EXISTS {SCHEMA}.scrape_runs (
        id            SERIAL PRIMARY KEY,
        group_id      INTEGER NOT NULL REFERENCES {SCHEMA}.scrape_groups (id),
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
        ON {SCHEMA}.scrape_runs (group_id);
"""

# Schema-DDL nur einmal pro Prozess und DSN ausführen — connect() wird vom
# Dashboard pro Callback aufgerufen, da darf kein DDL-Lock anfallen.
_schema_ready: set[str] = set()


def get_dsn() -> str:
    from scraper.config import get_database_url
    return get_database_url()


def connect(dsn: str | None = None, retries: int = CONNECT_RETRIES):
    """Autocommit-Verbindung mit search_path=orchestrator,public.

    Autocommit: jede Statusänderung ist ein einzelnes atomares Statement
    (Claim per UPDATE ... WHERE status='pending'), offene Transaktionen
    über Poll-Wartezeiten hinweg wären nur idle-in-transaction-Ballast.
    """
    dsn = dsn or get_dsn()
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(
                dsn,
                connect_timeout=10,
                options=f"-c search_path={SCHEMA},public",
            )
            conn.autocommit = True
            if dsn not in _schema_ready:
                ensure_schema(conn)
                _schema_ready.add(dsn)
            return conn
        except psycopg2.OperationalError as exc:
            if attempt == retries:
                raise
            logger.warning(
                "Queue-DB nicht erreichbar (Versuch %d/%d): %s — Retry in %.0fs",
                attempt, retries, exc, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, CONNECT_BACKOFF_CAP)


def ensure_schema(conn) -> None:
    """Idempotentes DDL — Spiegel von migrations/013_orchestrator_queue.sql."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL)


if __name__ == "__main__":
    conn = connect()
    conn.close()
    print(f"Schema '{SCHEMA}' bereit.")
