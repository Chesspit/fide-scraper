"""GAP-Perioden-Reparatur: only_period in Queue und Worker (Migration 019)."""

from datetime import date

import pytest

import orchestrator.worker as worker
from orchestrator.queue_manager import Group, QueueManager
from orchestrator.setup_db import connect


@pytest.fixture
def conn(data_db):
    c = connect()
    yield c
    c.close()


def test_claim_returns_only_period(data_db):
    gid = data_db.insert_group(federation="GAP", continent="GLOBAL")
    data_db.execute("UPDATE orchestrator.scrape_groups SET only_period='2024-06-01' WHERE id=%s",
                    (gid,))
    qm = QueueManager(dsn=data_db.dsn)
    try:
        group = qm.get_next_group()
    finally:
        qm.close()
    assert group.only_period == date(2024, 6, 1)


def test_get_fide_ids_only_period_selects_listed_unscraped(data_db, conn):
    for fid, active in [(1, True), (2, True), (3, True), (4, False)]:
        data_db.insert_player(fid, federation="ESP", std_rating=1600, active=active)
    data_db.insert_rating(1, "2024-06-01", num_games=5)   # fehlt → nachholen
    data_db.insert_rating(2, "2024-06-01", num_games=5)   # schon gescrapt
    data_db.insert_period(2, "2024-06-01")
    data_db.insert_rating(3, "2024-06-01", num_games=0)   # keine Partien laut Liste
    data_db.insert_rating(4, "2024-06-01", num_games=5)   # inaktiv
    data_db.insert_period(1, "2024-05-01")                # andere Periode zählt nicht

    ids = worker.get_fide_ids(conn, "GAP", 1, 9999, only_period="2024-06-01")
    assert ids == [1]


def test_scrape_group_only_period_limits_periods(data_db, conn, monkeypatch):
    data_db.insert_player(1, federation="ESP", std_rating=1600)
    data_db.insert_rating(1, "2024-06-01", num_games=5)
    seen = {}

    def fake_pending(_conn, periods, fide_ids=None):
        seen["periods"] = periods
        return []

    monkeypatch.setattr("scraper.db.get_pending_periods", fake_pending)
    group = Group(id=1, federation="GAP", continent="GLOBAL", year=2024, elo_min=1,
                  elo_max=9999, player_count=1, priority=1, only_period=date(2024, 6, 1))
    worker.scrape_group(group, conn, proxy_manager=None, profile={}, qm=None)
    assert seen["periods"] == ["2024-06-01"]
