"""qc/live.py — Einzelabfrage Spieler × Periode (QC-Bereich im Dashboard)."""

from datetime import date

from orchestrator.setup_db import connect
from qc.live import player_period

P = date(2024, 5, 1)


def _sev(r):
    return {s for s, _ in r["findings"]}


def test_green_combo(data_db):
    data_db.insert_player(1)
    data_db.insert_rating(1, "2024-04-01", published_rating=2000, num_games=0)
    data_db.insert_rating(1, "2024-05-01", published_rating=2013, num_games=2, std_rating=2000)
    data_db.insert_period(1, "2024-05-01", status="ok", k_factor=20)
    data_db.insert_game(1, "2024-05-01", 1, 12.8, result="1", rating_change=0.64,
                        opponent_rating=2100, tournament_name="A")
    data_db.insert_game(1, "2024-05-01", 2, 0.0, result="0.5", rating_change=0.0,
                        opponent_rating=2000, tournament_name="A")
    r = player_period(connect(), 1, P)
    assert _sev(r) == {"ok"}
    assert r["chain"]["ok"] and r["formula_checkable"]
    assert [g["formula_ok"] for g in r["games"]] == [True, True]


def test_missing_and_withdrawn(data_db):
    data_db.insert_player(2)
    data_db.insert_rating(2, "2024-05-01", published_rating=1500, num_games=3)
    assert "hard" in _sev(player_period(connect(), 2, P))
    # neuere Liste ohne Spieler 2 → zurückgezogen, nur noch INFO
    data_db.insert_player(3)
    data_db.insert_rating(3, "2024-06-01", published_rating=1800, num_games=0)
    assert _sev(player_period(connect(), 2, P)) == {"info"}


def test_fewer_games_and_bad_formula(data_db):
    data_db.insert_player(4)
    data_db.insert_rating(4, "2024-05-01", published_rating=2000, num_games=3, std_rating=2000)
    data_db.insert_period(4, "2024-05-01", status="ok")
    data_db.insert_game(4, "2024-05-01", 1, 10.0, result="1", rating_change=0.5,
                        opponent_rating=2100, tournament_name="A")
    r = player_period(connect(), 4, P)
    texts = " ".join(t for _, t in r["findings"])
    assert "1 Partien gescrapt, 3 laut Liste" in texts
    assert "Elo-Formel" in texts
