"""qc/audit_store.py — Audit-Lauf speichern, lesen, als AuditResult zurückholen.

Migration 021 wird aus der Datei eingespielt (wie test_audit.py mit 018).
Läuft einen echten (kleinen) Audit über run_audit, damit die JSONB-Form mit
der tatsächlichen AuditResult-Struktur übereinstimmt.
"""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from orchestrator.setup_db import connect
from qc.audit import run_audit
from qc.audit_store import get_run, list_runs, run_to_result, store_run, trend

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def conn(data_db):
    c = connect()
    with c.cursor() as cur:
        cur.execute((MIGRATIONS / "018_fide_expected_score.sql").read_text())
        cur.execute((MIGRATIONS / "021_qc_audit_runs.sql").read_text())
        cur.execute("TRUNCATE qc_audit_runs RESTART IDENTITY")
    yield c
    c.close()


def _small_audit(data_db):
    data_db.insert_player(1, federation="GER")
    data_db.insert_rating(1, "2024-04-01", published_rating=2000, num_games=0)
    data_db.insert_rating(1, "2024-05-01", published_rating=2000, num_games=2, std_rating=2000)
    # Soll 1 Kombo, nicht gescrapt → harter Befund in Ebene 1
    return run_audit(connect, date(2024, 5, 1), date(2024, 5, 1), jobs=1)


def test_store_and_read_back(data_db, conn):
    res = _small_audit(data_db)
    started = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    run_id = store_run(conn, res, layers={0, 1, 2, 3}, started_at=started,
                       duration_s=12.34, params={"sample": 20}, source="test")

    runs = list_runs(conn)
    assert [r["id"] for r in runs] == [run_id]
    assert runs[0]["has_hard"] is res.has_hard()
    assert runs[0]["headline"]["soll_combos"] == res.headline["soll_combos"]

    full = get_run(conn, run_id)
    assert full["source"] == "test" and float(full["duration_s"]) == 12.3
    assert {c["id"] for c in full["checks"]} == {c.id for c in res.checks}

    back = run_to_result(full)
    assert back.has_hard() == res.has_hard()
    assert back.check("periods_missing").n_findings == res.check("periods_missing").n_findings
    assert back.by_year.keys() == res.by_year.keys()


def test_trend_only_full_runs(data_db, conn):
    res = _small_audit(data_db)
    now = datetime.now(timezone.utc)
    store_run(conn, res, layers={0, 1, 2, 3}, started_at=now, duration_s=1)
    store_run(conn, res, layers={0, 1}, started_at=now, duration_s=1)
    res.federations = ["GER"]
    store_run(conn, res, layers={0, 1, 2, 3}, started_at=now, duration_s=1)
    assert len(trend(conn)) == 1
