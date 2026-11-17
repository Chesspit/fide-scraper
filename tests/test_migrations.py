"""core/migrations.py und scripts/migrate.py (Review F-18, Paket 6).

Reine Dateiprüfungen laufen immer; die DB-Tests nur mit ORCH_TEST_DATABASE_URL
(Wegwerf-PG, nie DATABASE_URL – siehe tests/test_db.py).
"""

import os
from pathlib import Path

import pytest

from core import migrations as mig

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

DB_URL = os.environ.get("ORCH_TEST_DATABASE_URL", "")
requires_db = pytest.mark.skipif(
    not DB_URL or not HAS_PSYCOPG2,
    reason="ORCH_TEST_DATABASE_URL not set or psycopg2 not installed",
)
DB = "fide_migrations_test"


# ── Dateien ───────────────────────────────────────────────────────────────────

def test_numbers_unique_and_ascending():
    ms = mig.list_migrations()
    numbers = [m.number for m in ms]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))
    assert ms[0].name == "001_initial.sql"


def test_concurrently_files_run_without_transaction():
    by_number = {m.number: m for m in mig.list_migrations()}
    for n in (15, 23, 24, 25, 27, 31):
        assert by_number[n].no_transaction, by_number[n].name
    for n in (1, 22, 30, 32):
        assert not by_number[n].no_transaction, by_number[n].name


def test_no_transaction_files_split_cleanly():
    """Jede no-transaction-Datei zerfällt in einzelne Befehle ohne $$-Körper."""
    for m in mig.list_migrations():
        if m.no_transaction:
            stmts = mig.split_statements(m.sql)
            assert stmts, m.name
            for s in stmts:
                assert ";" not in s.rstrip(";"), (m.name, s)


def test_split_statements():
    sql = "-- Kopf; mit Semikolon\nDROP INDEX CONCURRENTLY IF EXISTS a;\n\nDROP INDEX CONCURRENTLY IF EXISTS b;\n"
    assert mig.split_statements(sql) == [
        "DROP INDEX CONCURRENTLY IF EXISTS a",
        "DROP INDEX CONCURRENTLY IF EXISTS b",
    ]
    with pytest.raises(ValueError):
        mig.split_statements("CREATE FUNCTION f() RETURNS int AS $$ SELECT 1; $$ LANGUAGE sql;")


def test_duplicate_numbers_rejected(tmp_path: Path):
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "001_b.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError):
        mig.list_migrations(tmp_path)


def test_031_drops_only_the_five_measured_indexes():
    sql = (mig.MIGRATIONS_DIR / "031_drop_unused_indexes.sql").read_text()
    stmts = mig.split_statements(sql)
    assert stmts == [
        f"DROP INDEX CONCURRENTLY IF EXISTS {name}" for name in (
            "game_results_over_performance_idx",
            "game_results_opponent_sex_idx",
            "game_results_tournament_type_idx",
            "game_results_match_quality_idx",
            "idx_players_name_fed",
        )
    ]


# ── Datenbank ─────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {DB}")
        cur.execute(f"CREATE DATABASE {DB}")
    admin.close()
    c = psycopg2.connect(psycopg2.extensions.make_dsn(DB_URL, dbname=DB))
    c.autocommit = True
    yield c
    c.close()


def _indexes(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        return {r[0] for r in cur.fetchall()}


@requires_db
def test_fresh_database_all_migrations(conn):
    for m in mig.list_migrations():
        mig.ensure_table(conn)
        mig.apply(conn, m)
    states = {s for _, s in mig.status(conn)}
    assert states == {"applied"}
    idx = _indexes(conn)
    assert "game_results_over_performance_idx" not in idx
    assert "idx_players_name_fed" not in idx
    assert "idx_game_results_fide_period" in idx          # benutzte Indizes bleiben
    with conn.cursor() as cur:
        cur.execute("SELECT reloptions FROM pg_class WHERE relname = 'rating_history'")
        assert "autovacuum_vacuum_scale_factor=0.01" in cur.fetchone()[0]


@requires_db
def test_baseline_then_pending(conn):
    ms = mig.list_migrations()
    for m in ms:
        if m.number <= 30:
            mig.apply_sql(conn, m)          # «von Hand» eingespielt, nicht protokolliert
    assert mig.applied(conn) == {}
    marked = mig.baseline(conn, 30)
    assert [m.number for m in marked] == [m.number for m in ms if m.number <= 30]
    assert mig.baseline(conn, 30) == []    # zweites Mal: nichts mehr
    assert [m.number for m in mig.pending(conn)] == [m.number for m in ms if m.number > 30]
    with conn.cursor() as cur:
        cur.execute("SELECT bool_and(baseline) FROM schema_migrations")
        assert cur.fetchone()[0] is True


@requires_db
def test_failed_migration_leaves_nothing(conn, tmp_path: Path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE t1 (x int);")
    (tmp_path / "002_bad.sql").write_text("CREATE TABLE t2 (x int);\nSELECT * FROM gibt_es_nicht;")
    ms = mig.list_migrations(tmp_path)
    mig.ensure_table(conn)
    mig.apply(conn, ms[0])
    with pytest.raises(psycopg2.Error):
        mig.apply(conn, ms[1])
    assert [m.name for m in mig.pending(conn, ms)] == ["002_bad.sql"]
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('t2')")
        assert cur.fetchone()[0] is None    # Transaktion zurückgerollt


@requires_db
def test_changed_file_detected(conn, tmp_path: Path):
    f = tmp_path / "001_a.sql"
    f.write_text("CREATE TABLE t1 (x int);")
    ms = mig.list_migrations(tmp_path)
    mig.ensure_table(conn)
    mig.apply(conn, ms[0])
    f.write_text("CREATE TABLE t1 (x bigint);")
    assert mig.status(conn, mig.list_migrations(tmp_path))[0][1] == "changed"


@requires_db
def test_cli_refuses_existing_db_without_baseline(conn, capsys):
    from scripts import migrate
    mig.apply_sql(conn, mig.list_migrations()[0])     # players existiert, nichts protokolliert
    dsn = psycopg2.extensions.make_dsn(DB_URL, dbname=DB)
    assert migrate.main(["--dsn", dsn]) == 1
    assert "--baseline" in capsys.readouterr().out
    assert mig.applied(conn) == {}
