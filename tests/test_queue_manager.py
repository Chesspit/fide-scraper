"""Tests for orchestrator.queue_manager."""

import tempfile
import pytest
from pathlib import Path

from orchestrator.queue_manager import AUTO_RETRY_MAX, QueueManager, TIER_WIDTH
from orchestrator.setup_db import create_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_qm() -> tuple[QueueManager, Path]:
    """Return a QueueManager backed by a fresh in-memory-like temp DB."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = Path(tmp.name)
    tmp.close()
    return QueueManager(db_path=db_path), db_path


def _insert_group(db_path: Path, **kwargs) -> int:
    """Insert a minimal group row; return its id."""
    defaults = dict(
        federation="GER", continent="Europe", year=2024,
        elo_min=1400, elo_max=1799, player_count=150,
        status="pending", priority=1000,
        retries=0, last_run_at=None,
    )
    defaults.update(kwargs)
    conn = create_db(db_path)
    cur = conn.execute(
        """INSERT INTO scrape_groups
           (federation, continent, year, elo_min, elo_max,
            player_count, status, priority, retries, last_run_at)
           VALUES (:federation,:continent,:year,:elo_min,:elo_max,
                   :player_count,:status,:priority,:retries,:last_run_at)""",
        defaults,
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


# ---------------------------------------------------------------------------
# get_next_group
# ---------------------------------------------------------------------------

class TestGetNextGroup:
    def test_returns_none_when_queue_empty(self):
        qm, _ = _tmp_qm()
        assert qm.get_next_group() is None

    def test_returns_group_when_pending(self):
        qm, db = _tmp_qm()
        _insert_group(db)
        group = qm.get_next_group()
        assert group is not None
        assert group.federation == "GER"

    def test_skips_non_pending(self):
        qm, db = _tmp_qm()
        _insert_group(db, status="done")
        assert qm.get_next_group() is None

    def test_picks_from_top_tier(self):
        qm, db = _tmp_qm()
        high_id = _insert_group(db, priority=100)
        _insert_group(db, priority=100 + TIER_WIDTH + 1, federation="SUI")
        group = qm.get_next_group()
        assert group.id == high_id

    def test_both_tiers_eligible_when_within_width(self):
        qm, db = _tmp_qm()
        _insert_group(db, priority=100, federation="GER")
        _insert_group(db, priority=100 + TIER_WIDTH, federation="SUI")
        # Both are within the same tier — either may be returned
        group = qm.get_next_group()
        assert group is not None


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    def test_mark_running(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db)
        qm.mark_running(gid)
        conn = create_db(db)
        row = conn.execute("SELECT status FROM scrape_groups WHERE id=?", (gid,)).fetchone()
        assert row[0] == "running"

    def test_mark_done_sets_records(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db)
        qm.mark_done(gid, records_found=42)
        conn = create_db(db)
        row = conn.execute(
            "SELECT status, records_found FROM scrape_groups WHERE id=?", (gid,)
        ).fetchone()
        assert row[0] == "done"
        assert row[1] == 42

    def test_mark_failed_increments_retries(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db)
        qm.mark_failed(gid, "timeout")
        qm.mark_failed(gid, "timeout")
        conn = create_db(db)
        row = conn.execute("SELECT retries FROM scrape_groups WHERE id=?", (gid,)).fetchone()
        assert row[0] == 2

    def test_reset_to_pending(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db, status="failed")
        qm.reset_to_pending(gid)
        conn = create_db(db)
        row = conn.execute("SELECT status FROM scrape_groups WHERE id=?", (gid,)).fetchone()
        assert row[0] == "pending"

    def test_skip(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db)
        qm.skip(gid, reason="no data available")
        conn = create_db(db)
        row = conn.execute("SELECT status, notes FROM scrape_groups WHERE id=?", (gid,)).fetchone()
        assert row[0] == "skipped"
        assert "no data" in row[1]


# ---------------------------------------------------------------------------
# Auto-Retry (requeue_failed / count_exhausted_failed)
# ---------------------------------------------------------------------------

_OLD_TS = "2020-01-01T00:00:00"  # weit vor jeder min_age-Schwelle


class TestRequeueFailed:
    def test_requeues_failed_with_budget(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db, status="failed", retries=1, last_run_at=_OLD_TS)
        assert qm.requeue_failed() == 1
        conn = create_db(db)
        row = conn.execute("SELECT status, retries FROM scrape_groups WHERE id=?", (gid,)).fetchone()
        assert row[0] == "pending"
        assert row[1] == 1  # retries bleibt stehen — Budget wird nicht zurückgesetzt

    def test_respects_retry_cap(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db, status="failed", retries=AUTO_RETRY_MAX, last_run_at=_OLD_TS)
        assert qm.requeue_failed() == 0
        conn = create_db(db)
        row = conn.execute("SELECT status FROM scrape_groups WHERE id=?", (gid,)).fetchone()
        assert row[0] == "failed"

    def test_respects_min_age(self):
        import time as _time
        qm, db = _tmp_qm()
        recent = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime())
        gid = _insert_group(db, status="failed", retries=1, last_run_at=recent)
        assert qm.requeue_failed() == 0
        conn = create_db(db)
        row = conn.execute("SELECT status FROM scrape_groups WHERE id=?", (gid,)).fetchone()
        assert row[0] == "failed"

    def test_null_last_run_requeued_immediately(self):
        qm, db = _tmp_qm()
        _insert_group(db, status="failed", retries=0, last_run_at=None)
        assert qm.requeue_failed() == 1

    def test_ignores_non_failed(self):
        qm, db = _tmp_qm()
        _insert_group(db, status="done", retries=0, last_run_at=_OLD_TS)
        _insert_group(db, status="pending", federation="SUI", retries=0, last_run_at=_OLD_TS)
        assert qm.requeue_failed() == 0

    def test_count_exhausted_failed(self):
        qm, db = _tmp_qm()
        _insert_group(db, status="failed", retries=AUTO_RETRY_MAX, last_run_at=_OLD_TS)
        _insert_group(db, status="failed", federation="SUI", retries=1, last_run_at=_OLD_TS)
        assert qm.count_exhausted_failed() == 1


# ---------------------------------------------------------------------------
# Counts & stats
# ---------------------------------------------------------------------------

class TestCountsAndStats:
    def test_pending_count(self):
        qm, db = _tmp_qm()
        _insert_group(db, federation="GER")
        _insert_group(db, federation="SUI")
        _insert_group(db, federation="AUT", status="done")
        assert qm.pending_count() == 2

    def test_done_count(self):
        qm, db = _tmp_qm()
        _insert_group(db, federation="GER", status="done")
        _insert_group(db, federation="SUI", status="done")
        assert qm.done_count() == 2

    def test_stats_keys(self):
        qm, db = _tmp_qm()
        _insert_group(db, status="pending")
        _insert_group(db, status="done", federation="SUI")
        s = qm.stats()
        assert "pending" in s and "done" in s
        assert s["pending"] == 1 and s["done"] == 1


# ---------------------------------------------------------------------------
# get_wait_time
# ---------------------------------------------------------------------------

class TestGetWaitTime:
    def test_respects_minimum(self):
        qm, _ = _tmp_qm()
        profile = {"base_wait_seconds": 1.0, "jitter": 0.4, "min_wait_seconds": 0.5}
        for _ in range(50):
            assert qm.get_wait_time(profile) >= 0.5

    def test_stays_within_jitter_range(self):
        qm, _ = _tmp_qm()
        profile = {"base_wait_seconds": 3.0, "jitter": 0.5, "min_wait_seconds": 0.0}
        for _ in range(50):
            wait = qm.get_wait_time(profile)
            assert 1.5 <= wait <= 4.5  # base ± 50%


# ---------------------------------------------------------------------------
# log_run
# ---------------------------------------------------------------------------

class TestLogRun:
    def test_log_run_inserts_row(self):
        qm, db = _tmp_qm()
        gid = _insert_group(db)
        qm.log_run(gid, started_at="2026-05-09T10:00:00",
                   status="success", records_found=99)
        conn = create_db(db)
        row = conn.execute(
            "SELECT status, records_found FROM scrape_runs WHERE group_id=?", (gid,)
        ).fetchone()
        assert row[0] == "success"
        assert row[1] == 99
