"""Tests for orchestrator.store — Bucket-/Aggregations-Logik gegen Wegwerf-SQLite.

Genau diese Schicht (Config-Maps → SQL → Aggregation) war vor Review #6 in
app.py untestbar vergraben; der DC-UPDATE-1-Report-Bug (Thread fehlte still
in Tabelle UND MB-Summe) saß exakt hier.
"""

import tempfile
from pathlib import Path

import pytest

from orchestrator import store
from orchestrator.setup_db import create_db


@pytest.fixture
def db(monkeypatch) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = Path(tmp.name)
    tmp.close()
    create_db(db_path).close()
    monkeypatch.setattr(store, "DB_PATH", db_path)
    return db_path


def _insert_group(db_path: Path, **kwargs) -> int:
    defaults = dict(
        federation="GER", continent="Europe", year=2024,
        elo_min=1400, elo_max=1449, player_count=100,
        status="pending", priority=1,
    )
    defaults.update(kwargs)
    conn = create_db(db_path)
    cur = conn.execute(
        """INSERT INTO scrape_groups
           (federation, continent, year, elo_min, elo_max, player_count, status, priority)
           VALUES (:federation,:continent,:year,:elo_min,:elo_max,:player_count,:status,:priority)""",
        defaults,
    )
    conn.commit()
    gid = cur.lastrowid
    conn.close()
    return gid


def _insert_run(db_path: Path, group_id: int, **kwargs) -> None:
    defaults = dict(started_at="2026-07-01T10:00:00", finished_at="2026-07-01T11:00:00",
                    status="success", records_found=10, mb_downloaded=5.0, thread_slot=108)
    defaults.update(kwargs)
    conn = create_db(db_path)
    conn.execute(
        """INSERT INTO scrape_runs
           (group_id, started_at, finished_at, status, records_found, mb_downloaded, thread_slot)
           VALUES (:group_id,:started_at,:finished_at,:status,:records_found,:mb_downloaded,:thread_slot)""",
        {"group_id": group_id, **defaults},
    )
    conn.commit()
    conn.close()


class TestQueryOverview:
    FED_MAP = {"GER": "GER", "POL": "DC-DE", "UKR": "DC-DE"}

    def test_wide_band_split_into_buckets(self, db):
        """Ein 1400–1549-Band muss 3 50er-Buckets füllen, nicht einen."""
        _insert_group(db, elo_min=1400, elo_max=1549, status="done")
        rows = store.query_overview(self.FED_MAP, 1400, 2300)
        buckets = {r["elo_bucket"]: r for r in rows if r["federation"] == "GER"}
        assert set(buckets) == {1400, 1450, 1500}
        assert all(b["done_count"] == 1 and b["total"] == 1 for b in buckets.values())

    def test_dc_aggregation_pools_federations(self, db):
        """POL + UKR landen gemeinsam in der DC-DE-Spalte."""
        _insert_group(db, federation="POL", status="done")
        _insert_group(db, federation="UKR", status="pending")
        rows = store.query_overview(self.FED_MAP, 1400, 2300)
        dc = next(r for r in rows if r["federation"] == "DC-DE" and r["elo_bucket"] == 1400)
        assert dc["total"] == 2
        assert dc["done_count"] == 1

    def test_ceiling_and_floor_respected(self, db):
        _insert_group(db, elo_min=0, elo_max=2400)  # Drift-Puffer-Band
        rows = store.query_overview(self.FED_MAP, 1400, 2300)
        buckets = [r["elo_bucket"] for r in rows]
        assert min(buckets) == 1400
        assert max(buckets) < 2300

    def test_unmapped_federation_ignored(self, db):
        _insert_group(db, federation="FRA")
        assert store.query_overview(self.FED_MAP, 1400, 2300) == []

    def test_empty_map_returns_empty(self, db):
        assert store.query_overview({}, 1400, 2300) == []


class TestBerichtData:
    def test_slot_labels_applied_and_unknown_fallback(self, db):
        """Der DC-UPDATE-1-Bug-Regressionstest: jeder Slot muss auftauchen —
        bekannte mit Label, unbekannte als 'Slot-N' statt still zu verschwinden."""
        gid = _insert_group(db)
        _insert_run(db, gid, thread_slot=108, mb_downloaded=7.5)
        _insert_run(db, gid, thread_slot=999, mb_downloaded=1.5,
                    started_at="2026-07-02T10:00:00")
        rows = store.query_bericht_data({108: "DC-UPDATE-1"})
        by_label = {r["slot_label"]: r for r in rows}
        assert by_label["DC-UPDATE-1"]["mb"] == 7.5
        assert by_label["Slot-999"]["mb"] == 1.5  # unbekannt ≠ unsichtbar


class TestQueryQueue:
    def test_running_first_then_priority(self, db):
        _insert_group(db, federation="AAA", priority=5, status="pending", elo_min=1400)
        _insert_group(db, federation="BBB", priority=9, status="running", elo_min=1450)
        rows = store.query_queue(None, {}, {}, worker_threads=[])
        assert rows[0]["federation"] == "BBB"  # running zuerst, trotz Priorität

    def test_live_thread_marker(self, db):
        _insert_group(db, federation="POL", year=2024, elo_min=1400, elo_max=1449,
                      status="running")
        threads = [{"slot": 108, "current_group": "POL/2024/1400–1449"}]
        rows = store.query_queue(None, {108: "DC-UPDATE-1"}, {}, worker_threads=threads)
        assert rows[0]["thread_affinity"] == "▶ DC-UPDATE-1"


class TestGlobalStats:
    def test_counts_by_status(self, db):
        _insert_group(db, federation="A", status="done", elo_min=1400)
        _insert_group(db, federation="B", status="done", elo_min=1450)
        _insert_group(db, federation="C", status="failed", elo_min=1500)
        s = store.query_global_stats()
        assert s["total"] == 3 and s["done"] == 2 and s["failed"] == 1
