"""Shared fixtures — PG-Testdatenbank für die Orchestrator-Queue (Review #5).

Die Queue liegt seit Review #5 in PostgreSQL (Schema "orchestrator"); die
Queue-/Store-/Audit-Tests brauchen deshalb eine erreichbare PG-Instanz.

Die Test-DB kommt AUSSCHLIESSLICH aus ORCH_TEST_DATABASE_URL (Wegwerf-PG).
Es gibt bewusst keinen Rückgriff auf DATABASE_URL mehr: die zeigt über die
.env bei offenem Tunnel auf die Produktiv-DB (Review F-25; Vorfall 30.10.2026,
siehe tests/test_db.py). Ohne die Variable werden die DB-Tests übersprungen —
die übrigen Tests (Parser, Fetcher, …) laufen weiter.

Lokal z. B.:
  docker run -d --name fide-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55432:5432 postgres:16
  export ORCH_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/fide_orch_test
In der CI setzt .github/workflows/tests.yml die Variable.
"""

import os

import psycopg2
import psycopg2.extensions
import pytest


def _resolve_test_dsn() -> str | None:
    return os.environ.get("ORCH_TEST_DATABASE_URL") or None


def _ensure_test_db(dsn: str) -> None:
    """Test-Datenbank anlegen, falls sie noch nicht existiert.

    Der Verwaltungs-Zugriff geht auf dieselbe Instanz wie die Test-DB
    (Datenbank "postgres"), nie auf DATABASE_URL.
    """
    try:
        psycopg2.connect(dsn, connect_timeout=3).close()
        return
    except psycopg2.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    dbname = psycopg2.extensions.parse_dsn(dsn)["dbname"]
    admin = psycopg2.connect(psycopg2.extensions.make_dsn(dsn, dbname="postgres"),
                             connect_timeout=3)
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
            retries=0, last_run_at=None, update_only=0, claimed_by=None,
            device=None,
        )
        defaults.update(kwargs)
        row = self.fetchone(
            """INSERT INTO orchestrator.scrape_groups
               (federation, continent, year, elo_min, elo_max,
                player_count, status, priority, retries, last_run_at, update_only,
                claimed_by, device)
               VALUES (%(federation)s,%(continent)s,%(year)s,%(elo_min)s,%(elo_max)s,
                       %(player_count)s,%(status)s,%(priority)s,%(retries)s,%(last_run_at)s,
                       %(update_only)s,%(claimed_by)s,%(device)s)
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
        pytest.skip("ORCH_TEST_DATABASE_URL not set — DB-Tests übersprungen (Wegwerf-PG nötig)")
    try:
        _ensure_test_db(dsn)
        conn = psycopg2.connect(dsn, connect_timeout=3)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Test-DB aus ORCH_TEST_DATABASE_URL nicht erreichbar: {exc}")
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


# ── Scrape-Daten-Fixture (Coverage-/Integritäts-Tests) ───────────────────────
# Minimal-Nachbau der vier public-Tabellen in der Test-DB: nur die Spalten,
# die coverage.py/integrity.py abfragen, ohne FK-Constraints (Tests müssen
# gezielt inkonsistente Zustände seeden können).

_DATA_DDL = """
CREATE TABLE IF NOT EXISTS public.players (
    fide_id         INTEGER PRIMARY KEY,
    name            TEXT,
    federation      CHAR(3),
    std_rating      INTEGER,
    sex             CHAR(1),
    active          BOOLEAN DEFAULT TRUE,
    analysis_group  TEXT
);
CREATE TABLE IF NOT EXISTS public.scrape_periods (
    fide_id         INTEGER NOT NULL,
    period          DATE NOT NULL,
    status          TEXT NOT NULL,
    k_factor        INTEGER,
    http_status     INTEGER,
    no_data_reason  TEXT,
    scraped_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fide_id, period)
);
CREATE TABLE IF NOT EXISTS public.game_results (
    id                      BIGSERIAL PRIMARY KEY,
    fide_id                 INTEGER NOT NULL,
    period                  DATE NOT NULL,
    game_index              INTEGER,
    rating_change_weighted  NUMERIC(5,2)
);
CREATE TABLE IF NOT EXISTS public.rating_history (
    fide_id           INTEGER NOT NULL,
    period            DATE NOT NULL,
    published_rating  INTEGER,
    num_games         INTEGER,
    PRIMARY KEY (fide_id, period)
);
CREATE TABLE IF NOT EXISTS public.rating_corrections (
    fide_id     INTEGER NOT NULL,
    period      DATE NOT NULL,
    amount      INTEGER NOT NULL,
    corr_type   TEXT NOT NULL DEFAULT 'fide_one_off',
    PRIMARY KEY (fide_id, period, corr_type)
);
-- Spalten für audit.py (Ebene 3) — ALTER statt CREATE, weil bestehende
-- Test-DBs die Tabellen schon in der schmaleren Form haben.
ALTER TABLE public.rating_history ADD COLUMN IF NOT EXISTS std_rating INTEGER;
ALTER TABLE public.game_results
    ADD COLUMN IF NOT EXISTS result           TEXT,
    ADD COLUMN IF NOT EXISTS rating_change    NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS opponent_name    TEXT,
    ADD COLUMN IF NOT EXISTS opponent_rating  INTEGER,
    ADD COLUMN IF NOT EXISTS tournament_name  TEXT,
    ADD COLUMN IF NOT EXISTS tournament_start_date DATE;
ALTER TABLE public.rating_corrections ADD COLUMN IF NOT EXISTS source TEXT;
"""


class DataTestDB(QueueTestDB):
    """QueueTestDB + Insert-Helper für die Scrape-Daten-Tabellen."""

    def insert_player(self, fide_id: int, **kwargs) -> int:
        defaults = dict(name=f"Player {fide_id}", federation="GER",
                        std_rating=2000, sex="M", active=True, analysis_group=None)
        defaults.update(kwargs)
        self.execute(
            """INSERT INTO public.players
               (fide_id, name, federation, std_rating, sex, active, analysis_group)
               VALUES (%(fide_id)s,%(name)s,%(federation)s,%(std_rating)s,%(sex)s,
                       %(active)s,%(analysis_group)s)""",
            {"fide_id": fide_id, **defaults},
        )
        return fide_id

    def insert_period(self, fide_id: int, period: str, **kwargs) -> None:
        defaults = dict(status="ok", k_factor=20, http_status=None, no_data_reason=None)
        defaults.update(kwargs)
        self.execute(
            """INSERT INTO public.scrape_periods
               (fide_id, period, status, k_factor, http_status, no_data_reason)
               VALUES (%(fide_id)s,%(period)s,%(status)s,%(k_factor)s,
                       %(http_status)s,%(no_data_reason)s)""",
            {"fide_id": fide_id, "period": period, **defaults},
        )

    def insert_game(self, fide_id: int, period: str, game_index: int = 1,
                    rating_change_weighted: float = 2.5, **kwargs) -> None:
        defaults = dict(result="1", rating_change=None, opponent_name=None,
                        opponent_rating=None, tournament_name="T",
                        tournament_start_date=None)
        defaults.update(kwargs)
        self.execute(
            """INSERT INTO public.game_results
               (fide_id, period, game_index, rating_change_weighted, result,
                rating_change, opponent_name, opponent_rating, tournament_name,
                tournament_start_date)
               VALUES (%(fide_id)s,%(period)s,%(game_index)s,%(rcw)s,%(result)s,
                       %(rating_change)s,%(opponent_name)s,%(opponent_rating)s,
                       %(tournament_name)s,%(tournament_start_date)s)""",
            {"fide_id": fide_id, "period": period, "game_index": game_index,
             "rcw": rating_change_weighted, **defaults},
        )

    def insert_rating(self, fide_id: int, period: str,
                      published_rating: int = 2000, num_games: int | None = None,
                      std_rating: int | None = None) -> None:
        self.execute(
            """INSERT INTO public.rating_history
               (fide_id, period, published_rating, num_games, std_rating)
               VALUES (%s,%s,%s,%s,%s)""",
            (fide_id, period, published_rating, num_games, std_rating),
        )


@pytest.fixture
def data_db(queue_dsn, monkeypatch) -> DataTestDB:
    """Leere Queue- UND Scrape-Daten-Tabellen in der Test-DB."""
    monkeypatch.setenv("DATABASE_URL", queue_dsn)
    db = DataTestDB(queue_dsn)
    db.execute(_DATA_DDL)
    db.execute(
        "TRUNCATE orchestrator.scrape_runs, orchestrator.scrape_groups RESTART IDENTITY CASCADE"
    )
    db.execute(
        "TRUNCATE public.players, public.scrape_periods, public.game_results, "
        "public.rating_history, public.rating_corrections RESTART IDENTITY CASCADE"
    )
    return db
