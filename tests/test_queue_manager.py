"""Tests for orchestrator.queue_manager — gegen die PG-Test-DB (siehe conftest.py)."""

import pytest

from orchestrator.queue_manager import AUTO_RETRY_MAX, QueueManager, TIER_WIDTH


@pytest.fixture
def qm(queue_db):
    manager = QueueManager(dsn=queue_db.dsn)
    yield manager
    manager.close()


_OLD_TS = "2020-01-01T00:00:00"  # weit vor jeder min_age-Schwelle


# ---------------------------------------------------------------------------
# get_next_group
# ---------------------------------------------------------------------------

class TestGetNextGroup:
    def test_returns_none_when_queue_empty(self, qm):
        assert qm.get_next_group() is None

    def test_returns_group_when_pending(self, qm, queue_db):
        queue_db.insert_group()
        group = qm.get_next_group()
        assert group is not None
        assert group.federation == "GER"

    def test_claim_sets_running(self, qm, queue_db):
        gid = queue_db.insert_group()
        group = qm.get_next_group()
        assert group.id == gid
        row = queue_db.fetchone(
            "SELECT status, last_run_at FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "running"
        assert row[1] is not None

    def test_skips_non_pending(self, qm, queue_db):
        queue_db.insert_group(status="done")
        assert qm.get_next_group() is None

    def test_picks_from_top_tier(self, qm, queue_db):
        high_id = queue_db.insert_group(priority=100)
        queue_db.insert_group(priority=100 + TIER_WIDTH + 1, federation="SUI")
        group = qm.get_next_group()
        assert group.id == high_id

    def test_both_tiers_eligible_when_within_width(self, qm, queue_db):
        queue_db.insert_group(priority=100, federation="GER")
        queue_db.insert_group(priority=100 + TIER_WIDTH, federation="SUI")
        # Both are within the same tier — either may be returned
        group = qm.get_next_group()
        assert group is not None

    def test_dc_affinity_filter(self, qm, queue_db):
        queue_db.insert_group(federation="GER")  # residential
        dc_id = queue_db.insert_group(federation="IND", elo_min=1500)
        queue_db.execute(
            "UPDATE orchestrator.scrape_groups SET thread_affinity='dc_in' WHERE id=%s", (dc_id,))
        group = qm.get_next_group(dc_affinity="dc_in")
        assert group.id == dc_id
        # Residential-Modus darf den DC-Pool nicht anfassen
        group2 = qm.get_next_group()
        assert group2 is not None and group2.federation == "GER"


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    def test_mark_running(self, qm, queue_db):
        gid = queue_db.insert_group()
        qm.mark_running(gid)
        row = queue_db.fetchone(
            "SELECT status FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "running"

    def test_mark_done_sets_records(self, qm, queue_db):
        gid = queue_db.insert_group()
        qm.mark_done(gid, records_found=42)
        row = queue_db.fetchone(
            "SELECT status, records_found FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "done"
        assert row[1] == 42

    def test_mark_failed_increments_retries(self, qm, queue_db):
        gid = queue_db.insert_group()
        qm.mark_failed(gid, "timeout")
        qm.mark_failed(gid, "timeout")
        row = queue_db.fetchone(
            "SELECT retries FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == 2

    def test_reset_to_pending(self, qm, queue_db):
        gid = queue_db.insert_group(status="failed")
        qm.reset_to_pending(gid)
        row = queue_db.fetchone(
            "SELECT status FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "pending"

    def test_skip(self, qm, queue_db):
        gid = queue_db.insert_group()
        qm.skip(gid, reason="no data available")
        row = queue_db.fetchone(
            "SELECT status, notes FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "skipped"
        assert "no data" in row[1]


# ---------------------------------------------------------------------------
# Auto-Retry (requeue_failed / count_exhausted_failed)
# ---------------------------------------------------------------------------

class TestRequeueFailed:
    def test_requeues_failed_with_budget(self, qm, queue_db):
        gid = queue_db.insert_group(status="failed", retries=1, last_run_at=_OLD_TS)
        assert qm.requeue_failed() == 1
        row = queue_db.fetchone(
            "SELECT status, retries FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "pending"
        assert row[1] == 1  # retries bleibt stehen — Budget wird nicht zurückgesetzt

    def test_respects_retry_cap(self, qm, queue_db):
        gid = queue_db.insert_group(status="failed", retries=AUTO_RETRY_MAX, last_run_at=_OLD_TS)
        assert qm.requeue_failed() == 0
        row = queue_db.fetchone(
            "SELECT status FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "failed"

    def test_respects_min_age(self, qm, queue_db):
        # last_run_at = Serverzeit jetzt → jünger als jede min_age-Schwelle
        gid = queue_db.insert_group(status="failed", retries=1)
        queue_db.execute(
            "UPDATE orchestrator.scrape_groups SET last_run_at=localtimestamp WHERE id=%s", (gid,))
        assert qm.requeue_failed() == 0
        row = queue_db.fetchone(
            "SELECT status FROM orchestrator.scrape_groups WHERE id=%s", (gid,))
        assert row[0] == "failed"

    def test_null_last_run_requeued_immediately(self, qm, queue_db):
        queue_db.insert_group(status="failed", retries=0, last_run_at=None)
        assert qm.requeue_failed() == 1

    def test_ignores_non_failed(self, qm, queue_db):
        queue_db.insert_group(status="done", retries=0, last_run_at=_OLD_TS)
        queue_db.insert_group(status="pending", federation="SUI", retries=0, last_run_at=_OLD_TS)
        assert qm.requeue_failed() == 0

    def test_count_exhausted_failed(self, qm, queue_db):
        queue_db.insert_group(status="failed", retries=AUTO_RETRY_MAX, last_run_at=_OLD_TS)
        queue_db.insert_group(status="failed", federation="SUI", retries=1, last_run_at=_OLD_TS)
        assert qm.count_exhausted_failed() == 1


# ---------------------------------------------------------------------------
# Counts & stats
# ---------------------------------------------------------------------------

class TestCountsAndStats:
    def test_pending_count(self, qm, queue_db):
        queue_db.insert_group(federation="GER")
        queue_db.insert_group(federation="SUI")
        queue_db.insert_group(federation="AUT", status="done")
        assert qm.pending_count() == 2

    def test_done_count(self, qm, queue_db):
        queue_db.insert_group(federation="GER", status="done")
        queue_db.insert_group(federation="SUI", status="done")
        assert qm.done_count() == 2

    def test_stats_keys(self, qm, queue_db):
        queue_db.insert_group(status="pending")
        queue_db.insert_group(status="done", federation="SUI")
        s = qm.stats()
        assert "pending" in s and "done" in s
        assert s["pending"] == 1 and s["done"] == 1

    def test_reset_stale_running(self, qm, queue_db):
        queue_db.insert_group(status="running")
        queue_db.insert_group(status="running", federation="SUI")
        queue_db.insert_group(status="done", federation="AUT")
        assert qm.reset_stale_running() == 2
        assert qm.pending_count() == 2


# ---------------------------------------------------------------------------
# get_wait_time
# ---------------------------------------------------------------------------

class TestGetWaitTime:
    # get_wait_time braucht keine DB — QueueManager verbindet lazy
    def test_respects_minimum(self):
        qm = QueueManager()
        profile = {"base_wait_seconds": 1.0, "jitter": 0.4, "min_wait_seconds": 0.5}
        for _ in range(50):
            assert qm.get_wait_time(profile) >= 0.5

    def test_stays_within_jitter_range(self):
        qm = QueueManager()
        profile = {"base_wait_seconds": 3.0, "jitter": 0.5, "min_wait_seconds": 0.0}
        for _ in range(50):
            wait = qm.get_wait_time(profile)
            assert 1.5 <= wait <= 4.5  # base ± 50%


# ---------------------------------------------------------------------------
# log_run
# ---------------------------------------------------------------------------

class TestLogRun:
    def test_log_run_inserts_row(self, qm, queue_db):
        gid = queue_db.insert_group()
        qm.log_run(gid, started_at="2026-05-09T10:00:00",
                   status="success", records_found=99)
        row = queue_db.fetchone(
            "SELECT status, records_found FROM orchestrator.scrape_runs WHERE group_id=%s", (gid,))
        assert row[0] == "success"
        assert row[1] == 99
