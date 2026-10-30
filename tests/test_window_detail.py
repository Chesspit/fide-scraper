"""qc/window_detail.py — Abfragen der QC-Notebooks 08/10/11 gegen die Test-DB.

qc_rating_check kommt aus den Migrationen 004/006/016 (wie test_audit.py mit 018).
"""

from pathlib import Path

import pytest

from orchestrator.setup_db import connect
from qc import window_detail as qwd

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def conn(data_db):
    c = connect()
    with c.cursor() as cur:
        # connect() setzt search_path=orchestrator,public — die Migrationen gehören nach public
        cur.execute("DROP TABLE IF EXISTS orchestrator.qc_rating_check")
        cur.execute("SET search_path = public")
        for m in ("004_qc_table.sql", "006_qc_correction_column.sql", "016_qc_category.sql"):
            cur.execute((MIGRATIONS / m).read_text())
        cur.execute("TRUNCATE qc_rating_check")
        cur.execute("SET search_path = orchestrator, public")
    yield c
    c.close()


def _window(db, fide_id, start, end, delta, flag):
    db.execute(
        "INSERT INTO qc_rating_check (fide_id, period_start, period_end, published_start, "
        "published_end, expected_change, scraped_change, delta, flag) "
        "VALUES (%s, %s, %s, 2000, 2010, 10, %s, %s, %s)",
        (fide_id, start, end, 10 - delta, delta, flag),
    )


def test_windows_with_elo_group(data_db, conn):
    data_db.insert_player(1, std_rating=2450)
    data_db.insert_player(2, std_rating=1500)
    data_db.insert_rating(2, "2022-12-01", published_rating=2100, std_rating=2100)
    _window(data_db, 1, "2016-01-01", "2016-02-01", -3, "ok")
    _window(data_db, 2, "2016-01-01", "2016-02-01", 20, "error")
    _window(data_db, 2, "2014-01-01", "2014-02-01", 20, "error")   # vor 2015 → raus
    df = qwd.windows_with_elo_group(conn)
    assert len(df) == 2
    groups = dict(zip(df["fide_id"], df["elo_group"]))
    # Spieler 2: Rating zum Stichtag (rating_history) schlägt players.std_rating
    assert groups == {1: "ELO >= 2400", 2: "ELO 2000-2400"}
    assert df["period"].tolist() == ["16-01", "16-01"]
    assert sorted(df["abs_delta"]) == [3, 20]


def test_players_and_details(data_db, conn):
    data_db.insert_player(5, analysis_group="female_top")
    data_db.insert_player(6)
    data_db.insert_player(7)
    _window(data_db, 5, "2024-02-01", "2024-03-01", 8, "warn")
    _window(data_db, 6, "2024-05-01", "2024-06-01", 30, "error")
    _window(data_db, 7, "2024-05-01", "2024-06-01", 1, "ok")
    players = qwd.players_with_findings(conn, 2024)
    assert players.set_index("fide_id")["analysis_group"].to_dict() == {
        5: "female_top", 6: qwd.NO_GROUP}
    assert players.set_index("fide_id")["has_error"].to_dict() == {5: False, 6: True}

    data_db.insert_rating(5, "2024-02-01", published_rating=2000)
    data_db.insert_rating(5, "2024-03-01", published_rating=2010)
    data_db.insert_game(5, "2024-03-01", 1, 1.25)
    data_db.insert_game(5, "2024-03-01", 2, 0.5)
    data_db.execute("INSERT INTO rating_corrections (fide_id, period, amount) "
                    "VALUES (5, '2024-03-01', 7)")
    months = qwd.month_starts("2024-02-01", "2024-03-01")
    assert months == ["2024-02-01", "2024-03-01"]
    assert len(qwd.published_ratings(conn, [5, 6], months)) == 2
    games = qwd.game_deltas(conn, [5], months)
    assert games["game_delta"].tolist() == [1.8]   # ROUND(1.75, 1)
    assert qwd.corrections(conn, [5, 6])["fide_corr"].tolist() == [7]
