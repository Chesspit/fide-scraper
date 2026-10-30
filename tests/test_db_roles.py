"""Migration 022 (DB-Rollen, Phase 2a) auf einer frischen Wegwerf-DB.

Prüft die Rechte-Matrix aus docs/db_rollen.md: reader liest nur, qc schreibt
nur qc_*, ingest schreibt Rohdaten + Queue, aber keine QC-Ergebnisse. Und dass
orchestrator.setup_db.connect() als fide_ingest ohne Eigentümerrechte startet.
Nur ORCH_TEST_DATABASE_URL (Wegwerf-PG), nie DATABASE_URL.
"""

import os
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DB_URL = os.environ.get("ORCH_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="ORCH_TEST_DATABASE_URL not set")

DB = "fide_roles_test"
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture(scope="module")
def dsn():
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {DB}")
        cur.execute(f"CREATE DATABASE {DB}")
    admin.close()
    dsn = psycopg2.extensions.make_dsn(DB_URL, dbname=DB)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        for m in sorted(MIGRATIONS.glob("*.sql")):
            cur.execute(m.read_text())
        cur.execute((MIGRATIONS / "022_db_roles.sql").read_text())   # idempotent
        for role in ("fide_ingest", "fide_qc", "fide_reader"):
            cur.execute(f"ALTER ROLE {role} LOGIN")
        cur.execute("INSERT INTO players (fide_id, name) VALUES (1, 'A')")
    conn.close()
    return dsn


def _as(dsn, role):
    c = psycopg2.connect(psycopg2.extensions.make_dsn(dsn, user=role))
    c.autocommit = True
    return c


def _can(dsn, role, sql) -> bool:
    c = _as(dsn, role)
    try:
        with c.cursor() as cur:
            cur.execute(sql)
        return True
    except psycopg2.errors.InsufficientPrivilege:
        return False
    finally:
        c.close()


QC_INSERT = ("INSERT INTO qc_audit_runs (started_at, since_period, layers, has_hard, headline, "
             "checks) VALUES (now(), '2024-01-01', '{0}', false, '{}', '[]')")


@pytest.mark.parametrize("role,sql,allowed", [
    ("fide_reader", "SELECT count(*) FROM game_results", True),
    ("fide_reader", "SELECT fn_fide_expected(100)", True),
    ("fide_reader", "UPDATE players SET name = 'B' WHERE fide_id = 1", False),
    ("fide_reader", QC_INSERT, False),
    ("fide_reader", "SELECT count(*) FROM orchestrator.scrape_groups", False),
    ("fide_qc", QC_INSERT, True),
    ("fide_qc", "TRUNCATE qc_rating_check", True),
    ("fide_qc", "SELECT count(*) FROM orchestrator.scrape_groups", True),
    ("fide_qc", "UPDATE players SET name = 'B' WHERE fide_id = 1", False),
    ("fide_qc", "DELETE FROM scrape_periods", False),
    ("fide_ingest", "UPDATE players SET name = 'B' WHERE fide_id = 1", True),
    ("fide_ingest", "INSERT INTO rating_history (fide_id, period, published_rating) "
                    "VALUES (1, '2024-01-01', 2000)", True),
    ("fide_ingest", "DELETE FROM orchestrator.scrape_runs", True),
    ("fide_ingest", QC_INSERT, False),
])
def test_rights_matrix(dsn, role, sql, allowed):
    assert _can(dsn, role, sql) is allowed


def test_orchestrator_connect_as_ingest(dsn):
    from orchestrator import setup_db
    ingest = psycopg2.extensions.make_dsn(dsn, user="fide_ingest")
    conn = setup_db.connect(ingest, retries=1)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM scrape_groups")
        assert cur.fetchone()[0] == 0
    conn.close()
