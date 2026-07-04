"""Shared fixtures — PG-Testdatenbank für die Orchestrator-Queue (Review #5).

Die Queue liegt seit Review #5 in PostgreSQL (Schema "orchestrator"); die
Queue-/Store-Tests brauchen deshalb eine erreichbare PG-Instanz statt der
früheren Wegwerf-SQLite. DSN-Auflösung:

  1. ORCH_TEST_DATABASE_URL (explizit gesetzt)
  2. aus DATABASE_URL (.env) abgeleitet: gleiche Instanz, Datenbank
     'fide_orch_test' (wird bei Bedarf automatisch angelegt)

Ist keine PG erreichbar (z.B. SSH-Tunnel down), werden die Queue-Tests
geskippt — die übrigen Tests (Parser, Fetcher, …) laufen weiter.
"""

import os

import psycopg2
import pytest


def _resolve_test_dsn() -> str | None:
    dsn = os.environ.get("ORCH_TEST_DATABASE_URL")
    if dsn:
        return dsn
    try:
        from scraper.config import get_database_url
        base = get_database_url()
    except Exception:
        return None
    root, _, _dbname = base.rpartition("/")
    return f"{root}/fide_orch_test"


def _ensure_test_db(dsn: str) -> None:
    """Test-Datenbank anlegen, falls sie noch nicht existiert."""
    try:
        psycopg2.connect(dsn, connect_timeout=3).close()
        return
    except psycopg2.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    from scraper.config import get_database_url
    dbname = dsn.rpartition("/")[2]
    admin = psycopg2.connect(get_database_url(), connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()


class QueueTestDB:
    """Dünner Helfer um die Test-DB: Insert-Helpers + Roh-SQL."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def execute(self, sql: str, params: tuple = ()):
        conn = psycopg2.connect(self.dsn)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description:
                    return cur.fetchall()
                return None
        finally:
            conn.close()

    def fetchone(self, sql: str, params: tuple = ()):
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def insert_group(self, **kwargs) -> int:
        defaults = dict(
            federation="GER", continent="Europe", year=2024,
            elo_min=1400, elo_max=1799, player_count=150,
            status="pending", priority=1000,
            retries=0, last_run_at=None,
        )
        defaults.update(kwargs)
        row = self.fetchone(
            """INSERT INTO orchestrator.scrape_groups
               (federation, continent, year, elo_min, elo_max,
                player_count, status, priority, retries, last_run_at)
               VALUES (%(federation)s,%(continent)s,%(year)s,%(elo_min)s,%(elo_max)s,
                       %(player_count)s,%(status)s,%(priority)s,%(retries)s,%(last_run_at)s)
               RETURNING id""",
            defaults,
        )
        return row[0]

    def insert_run(self, group_id: int, **kwargs) -> None:
        defaults = dict(started_at="2026-07-01T10:00:00", finished_at="2026-07-01T11:00:00",
                        status="success", records_found=10, mb_downloaded=5.0, thread_slot=108)
        defaults.update(kwargs)
        self.execute(
            """INSERT INTO orchestrator.scrape_runs
               (group_id, started_at, finished_at, status, records_found, mb_downloaded, thread_slot)
               VALUES (%(group_id)s,%(started_at)s,%(finished_at)s,%(status)s,
                       %(records_found)s,%(mb_downloaded)s,%(thread_slot)s)""",
            {"group_id": group_id, **defaults},
        )


@pytest.fixture(scope="session")
def queue_dsn() -> str:
    dsn = _resolve_test_dsn()
    if dsn is None:
        pytest.skip("Keine DATABASE_URL konfiguriert — Queue-Tests übersprungen")
    try:
        _ensure_test_db(dsn)
        conn = psycopg2.connect(dsn, connect_timeout=3)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Queue-Test-DB nicht erreichbar (Tunnel down?): {exc}")
    from orchestrator.setup_db import ensure_schema
    conn.autocommit = True
    ensure_schema(conn)
    conn.close()
    return dsn


@pytest.fixture
def queue_db(queue_dsn, monkeypatch) -> QueueTestDB:
    """Leere Queue-Tabellen + DATABASE_URL auf die Test-DB umgebogen.

    Das Umbiegen sorgt dafür, dass store.get_conn() (liest DATABASE_URL pro
    Aufruf) und QueueManager() ohne DSN-Argument in Tests nie versehentlich
    die echte fidedb treffen.
    """
    monkeypatch.setenv("DATABASE_URL", queue_dsn)
    db = QueueTestDB(queue_dsn)
    db.execute(
        "TRUNCATE orchestrator.scrape_runs, orchestrator.scrape_groups RESTART IDENTITY CASCADE"
    )
    return db
