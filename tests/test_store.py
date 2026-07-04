"""Tests for orchestrator.store — Bucket-/Aggregations-Logik gegen die PG-Test-DB.

Genau diese Schicht (Config-Maps → SQL → Aggregation) war vor Review #6 in
app.py untestbar vergraben; der DC-UPDATE-1-Report-Bug (Thread fehlte still
in Tabelle UND MB-Summe) saß exakt hier. Seit Review #5 laufen die Tests
gegen PostgreSQL (fixture queue_db biegt DATABASE_URL um, siehe conftest.py).
"""

from orchestrator import store


class TestQueryOverview:
    FED_MAP = {"GER": "GER", "POL": "DC-DE", "UKR": "DC-DE"}

    def test_wide_band_split_into_buckets(self, queue_db):
        """Ein 1400–1549-Band muss 3 50er-Buckets füllen, nicht einen."""
        queue_db.insert_group(elo_min=1400, elo_max=1549, status="done")
        rows = store.query_overview(self.FED_MAP, 1400, 2300)
        buckets = {r["elo_bucket"]: r for r in rows if r["federation"] == "GER"}
        assert set(buckets) == {1400, 1450, 1500}
        assert all(b["done_count"] == 1 and b["total"] == 1 for b in buckets.values())

    def test_dc_aggregation_pools_federations(self, queue_db):
        """POL + UKR landen gemeinsam in der DC-DE-Spalte."""
        queue_db.insert_group(federation="POL", elo_max=1449, status="done")
        queue_db.insert_group(federation="UKR", elo_max=1449, status="pending")
        rows = store.query_overview(self.FED_MAP, 1400, 2300)
        dc = next(r for r in rows if r["federation"] == "DC-DE" and r["elo_bucket"] == 1400)
        assert dc["total"] == 2
        assert dc["done_count"] == 1

    def test_ceiling_and_floor_respected(self, queue_db):
        queue_db.insert_group(elo_min=0, elo_max=2400)  # Drift-Puffer-Band
        rows = store.query_overview(self.FED_MAP, 1400, 2300)
        buckets = [r["elo_bucket"] for r in rows]
        assert min(buckets) == 1400
        assert max(buckets) < 2300

    def test_unmapped_federation_ignored(self, queue_db):
        queue_db.insert_group(federation="FRA")
        assert store.query_overview(self.FED_MAP, 1400, 2300) == []

    def test_empty_map_returns_empty(self, queue_db):
        assert store.query_overview({}, 1400, 2300) == []


class TestBerichtData:
    def test_slot_labels_applied_and_unknown_fallback(self, queue_db):
        """Der DC-UPDATE-1-Bug-Regressionstest: jeder Slot muss auftauchen —
        bekannte mit Label, unbekannte als 'Slot-N' statt still zu verschwinden."""
        gid = queue_db.insert_group()
        queue_db.insert_run(gid, thread_slot=108, mb_downloaded=7.5)
        queue_db.insert_run(gid, thread_slot=999, mb_downloaded=1.5,
                            started_at="2026-07-02T10:00:00")
        rows = store.query_bericht_data({108: "DC-UPDATE-1"})
        by_label = {r["slot_label"]: r for r in rows}
        assert by_label["DC-UPDATE-1"]["mb"] == 7.5
        assert by_label["Slot-999"]["mb"] == 1.5  # unbekannt ≠ unsichtbar
        assert by_label["DC-UPDATE-1"]["day"] == "2026-07-01"  # ISO-String, kein date-Objekt


class TestQueryQueue:
    def test_running_first_then_priority(self, queue_db):
        queue_db.insert_group(federation="AAA", priority=5, status="pending", elo_min=1400)
        queue_db.insert_group(federation="BBB", priority=9, status="running", elo_min=1450)
        rows = store.query_queue(None, {}, {}, worker_threads=[])
        assert rows[0]["federation"] == "BBB"  # running zuerst, trotz Priorität

    def test_live_thread_marker(self, queue_db):
        queue_db.insert_group(federation="POL", year=2024, elo_min=1400, elo_max=1449,
                              status="running")
        threads = [{"slot": 108, "current_group": "POL/2024/1400–1449"}]
        rows = store.query_queue(None, {108: "DC-UPDATE-1"}, {}, worker_threads=threads)
        assert rows[0]["thread_affinity"] == "▶ DC-UPDATE-1"


class TestQueryCompleted:
    def test_duration_and_rate_from_run(self, queue_db):
        """julianday()-Ersatz: 2h-Lauf → duration_h=2.0, rate = records/h."""
        gid = queue_db.insert_group(status="done")
        queue_db.insert_run(gid, started_at="2026-07-01T10:00:00",
                            finished_at="2026-07-01T12:00:00", records_found=100)
        rows = store.query_completed({108: "DC-UPDATE-1"})
        assert len(rows) == 1
        assert rows[0]["duration_h"] == 2.0
        assert rows[0]["rate_per_h"] == 50.0
        assert rows[0]["thread_slot"] == "DC-UPDATE-1"


class TestGlobalStats:
    def test_counts_by_status(self, queue_db):
        queue_db.insert_group(federation="A", status="done", elo_min=1400)
        queue_db.insert_group(federation="B", status="done", elo_min=1450)
        queue_db.insert_group(federation="C", status="failed", elo_min=1500)
        s = store.query_global_stats()
        assert s["total"] == 3 and s["done"] == 2 and s["failed"] == 1
