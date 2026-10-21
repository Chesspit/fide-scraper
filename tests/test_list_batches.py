"""Listen-Gruppen (update_only=3): Auswahl nach offizieller Liste statt players.active."""

from datetime import date

import pytest

import orchestrator.worker as worker
from orchestrator.generate_list_batches import build_bands, federation_threads
from orchestrator.list_population import UPDATE_ONLY_LIST
from orchestrator.queue_manager import Group
from orchestrator.setup_db import connect

LATEST = "2026-10-01"


@pytest.fixture
def conn(data_db):
    c = connect()
    yield c
    c.close()


def _listed(data_db, fid, rating=1600):
    """Spieler steht in der neuesten Liste (sonst gilt er als 'erklärt')."""
    data_db.insert_rating(fid, LATEST, published_rating=rating, num_games=0)


def test_get_fide_ids_list_based_ignores_active_and_std_rating(data_db, conn):
    data_db.insert_player(1, federation="ESP", std_rating=1600, active=True)
    data_db.insert_player(2, federation="ESP", std_rating=0, active=False)   # inaktiv, unbewertet
    data_db.insert_player(3, federation="ESP", std_rating=1600, active=True)
    data_db.insert_player(4, federation="ESP", std_rating=1600, active=False)
    data_db.insert_player(5, federation="FRA", std_rating=1600, active=True)
    for fid in (1, 2, 3, 5):
        _listed(data_db, fid)
    for fid in (1, 2, 3, 4, 5):
        data_db.insert_rating(fid, "2022-03-01", published_rating=1600, num_games=7)
    data_db.insert_period(3, "2022-03-01")      # schon gescrapt
    # 4 fehlt in der neuesten Liste → erklärt; 5 andere Föderation

    ids = worker.get_fide_ids(conn, "ESP", 0, 9999, list_based=True,
                              period_from="2022-01-01", period_to="2022-12-01")
    assert ids == [1, 2]


def test_get_fide_ids_list_based_bands_on_published_rating(data_db, conn):
    data_db.insert_player(1, federation="ESP", std_rating=2100)   # heute 2100, damals 1500
    _listed(data_db, 1, rating=2100)
    data_db.insert_rating(1, "2022-03-01", published_rating=1450, num_games=4)
    data_db.insert_rating(1, "2022-04-01", published_rating=1500, num_games=4)

    def ids(lo, hi):
        return worker.get_fide_ids(conn, "ESP", lo, hi, list_based=True,
                                   period_from="2022-01-01", period_to="2022-12-01")

    assert ids(1500, 1599) == [1]
    assert ids(2000, 2199) == []


def test_scrape_group_list_based_fetches_only_soll_combos(data_db, conn, monkeypatch):
    data_db.insert_player(1, federation="ESP", std_rating=0, active=False)
    _listed(data_db, 1)
    data_db.insert_rating(1, "2025-01-01", published_rating=1600, num_games=3)  # Soll, offen
    data_db.insert_rating(1, "2025-03-01", published_rating=1600, num_games=0)  # keine Partien
    # übrige Monate 2025: kein Listeneintrag
    seen = {}

    class Stop(Exception):
        pass

    def fake_write_state(**kw):
        seen.update(kw)
        raise Stop

    monkeypatch.setattr(worker, "write_state", fake_write_state)
    group = Group(id=1, federation="ESP", continent="Europe", year=2025, elo_min=0,
                  elo_max=9999, player_count=1, priority=1, update_only=UPDATE_ONLY_LIST)
    with pytest.raises(Stop):
        worker.scrape_group(group, conn, proxy_manager=None, profile={}, qm=None)

    assert seen["combos_total"] == 1
    # Nicht-Soll-Monate werden weder abgefragt noch als no_data markiert
    assert data_db.fetchone("SELECT count(*) FROM public.scrape_periods")[0] == 0


def test_build_bands_contiguous_and_avoids_taken():
    players = [(2000, 900), (1900, 900), (1800, 900), (1700, 900), (1600, 100)]
    bands = build_bands(players, taken={1800}, target=1800)
    assert bands[0]["elo_max"] == 9999
    assert bands[-1]["elo_min"] == 0
    for upper, lower in zip(bands, bands[1:]):
        assert lower["elo_max"] == upper["elo_min"] - 1
    assert 1800 not in [b["elo_min"] for b in bands]
    assert sum(b["combos"] for b in bands) == 3700
    assert sum(b["player_count"] for b in bands) == 5


def test_federation_threads_uses_backfill_majority(data_db, conn):
    for elo, thread in [(1400, "dc_es"), (1500, "dc_es"), (1600, "dc_update_1")]:
        gid = data_db.insert_group(federation="ESP", continent="Europe", elo_min=elo,
                                   elo_max=elo + 99)
        data_db.execute("UPDATE orchestrator.scrape_groups SET thread_affinity=%s WHERE id=%s",
                        (thread, gid))
    gid = data_db.insert_group(federation="P3", continent="GLOBAL", elo_min=0, elo_max=2299)
    data_db.execute("UPDATE orchestrator.scrape_groups SET thread_affinity='dc_us' WHERE id=%s",
                    (gid,))
    with conn.cursor() as cur:
        assert federation_threads(cur) == {"ESP": "dc_es"}


def test_balance_by_region_stays_in_region():
    from orchestrator.generate_list_batches import THREAD_REGION, balance_by_region
    groups = [{"thread_affinity": "dc_uk", "combos": c} for c in (900, 800, 700, 600)]
    groups.append({"thread_affinity": "dc_in", "combos": 5000})
    balance_by_region(groups)
    europe = {g["thread_affinity"] for g in groups[:4]}
    assert all(THREAD_REGION[t] == "Europa" for t in europe)
    assert len(europe) == 4                       # auf vier Europa-Threads verteilt
    assert THREAD_REGION[groups[4]["thread_affinity"]] == "Asien"
