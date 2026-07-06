"""Tests für orchestrator/coverage.py — Ground-Truth-Coverage in 3 Dimensionen."""

import pytest

from orchestrator.coverage import (
    coverage_by_analysis_group,
    coverage_by_elo_band,
    coverage_by_federation,
)
from orchestrator.setup_db import connect
from orchestrator.sync_done_groups import valid_periods_for_year


@pytest.fixture
def conn(data_db):
    c = connect()
    yield c
    c.close()


def _row(rows, **match):
    hits = [r for r in rows if all(r[k] == v for k, v in match.items())]
    assert len(hits) == 1, f"expected exactly 1 row for {match}, got {len(hits)}"
    return hits[0]


class TestFederationDimension:
    def test_counts_players_periods_games(self, data_db, conn):
        data_db.insert_player(1, federation="GER")
        data_db.insert_player(2, federation="GER")
        data_db.insert_player(3, federation="AUT")
        # Spieler 1: 2 ok-Perioden + Partien; Spieler 2: nur no_data
        data_db.insert_period(1, "2024-01-01", status="ok")
        data_db.insert_period(1, "2024-02-01", status="ok")
        data_db.insert_game(1, "2024-01-01", game_index=1)
        data_db.insert_game(1, "2024-01-01", game_index=2)
        data_db.insert_period(2, "2024-01-01", status="no_data")

        rows = coverage_by_federation(conn, year_from=2024, year_to=2024)
        ger = _row(rows, federation="GER", year=2024)
        assert ger["players_active"] == 2
        assert ger["players_scraped"] == 1          # nur Spieler 1 hat ok
        assert ger["pct_players"] == 50.0
        assert ger["periods_ok"] == 2
        assert ger["periods_attempted"] == 3        # 2 ok + 1 no_data
        assert ger["periods_expected"] == 2 * 12    # 2024 = 12 gültige Perioden
        assert ger["games"] == 2

        aut = _row(rows, federation="AUT", year=2024)
        assert aut["players_scraped"] == 0
        assert aut["games"] == 0

    def test_inactive_players_excluded(self, data_db, conn):
        data_db.insert_player(1, federation="GER", active=False)
        data_db.insert_period(1, "2024-01-01", status="ok")
        rows = coverage_by_federation(conn, year_from=2024, year_to=2024)
        assert rows == []   # inaktiv → zählt weder als Soll noch als Ist

    def test_quarterly_year_pre_2012(self, data_db, conn):
        # 2011: nur 6 gültige Perioden (Jan/Mär/Mai/Jul/Sep/Nov)
        assert len(valid_periods_for_year(2011)) == 6
        data_db.insert_player(1, federation="GER")
        for p in valid_periods_for_year(2011):
            data_db.insert_period(1, p, status="ok")
        rows = coverage_by_federation(conn, year_from=2011, year_to=2011)
        ger = _row(rows, federation="GER", year=2011)
        assert ger["periods_expected"] == 6
        assert ger["pct_periods"] == 100.0


class TestAnalysisGroupDimension:
    def test_groups_reported_null_hidden(self, data_db, conn):
        data_db.insert_player(1, analysis_group="female_top")
        data_db.insert_player(2, analysis_group="male_control")
        data_db.insert_player(3, analysis_group=None)  # nicht in Analysegruppen
        data_db.insert_period(1, "2024-01-01", status="ok")
        data_db.insert_game(1, "2024-01-01")

        rows = coverage_by_analysis_group(conn, year_from=2024, year_to=2024)
        groups = {r["analysis_group"] for r in rows}
        assert groups == {"female_top", "male_control"}
        ft = _row(rows, analysis_group="female_top", year=2024)
        assert ft["players_scraped"] == 1
        assert ft["games"] == 1


class TestEloBandDimension:
    def test_banding_by_current_rating(self, data_db, conn):
        data_db.insert_player(1, std_rating=2450)
        data_db.insert_player(2, std_rating=2499)
        data_db.insert_player(3, std_rating=2500)
        data_db.insert_period(1, "2024-01-01", status="ok")

        rows = coverage_by_elo_band(conn, band_width=100, year_from=2024, year_to=2024)
        b2400 = _row(rows, elo_band=2400, year=2024)
        assert b2400["players_active"] == 2
        assert b2400["players_scraped"] == 1
        b2500 = _row(rows, elo_band=2500, year=2024)
        assert b2500["players_active"] == 1

    def test_null_rating_excluded(self, data_db, conn):
        data_db.insert_player(1, std_rating=None)
        rows = coverage_by_elo_band(conn, year_from=2024, year_to=2024)
        assert rows == []
