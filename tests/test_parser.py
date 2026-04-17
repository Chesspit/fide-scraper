"""Tests for scraper.parser against real FIDE HTML fixtures."""

from pathlib import Path

from scraper.parser import parse_calculations

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestParseCalculations:
    """Tests against calc_24171760_2025-04-01.html (Lei Tingjie, April 2025)."""

    def setup_method(self):
        html = load_fixture("calc_24171760_2025-04-01.html")
        self.games, self.k_factor, self.own_rating = parse_calculations(
            html, 24171760, "2025-04-01"
        )

    def test_game_count(self):
        assert len(self.games) == 17

    def test_k_factor(self):
        assert self.k_factor == 10

    def test_own_rating(self):
        assert self.own_rating == 2508

    def test_first_game_opponent(self):
        g = self.games[0]
        assert g["opponent_name"] == "Novozhilov, Semen"
        assert g["opponent_rating"] == 2344
        assert g["opponent_federation"] == "RUS"

    def test_first_game_result(self):
        g = self.games[0]
        assert g["result"] == "0.5"
        assert g["rating_change"] == -0.22
        assert g["rating_change_weighted"] == -2.2

    def test_first_game_color(self):
        assert self.games[0]["color"] == "B"  # black_note

    def test_second_game_color(self):
        assert self.games[1]["color"] == "W"  # white_note

    def test_game_index_sequential(self):
        indices = [g["game_index"] for g in self.games]
        assert indices == list(range(17))

    def test_fide_id_on_all_games(self):
        assert all(g["fide_id"] == 24171760 for g in self.games)

    def test_period_on_all_games(self):
        assert all(g["period"] == "2025-04-01" for g in self.games)

    def test_two_tournaments(self):
        tournament_names = {g["tournament_name"] for g in self.games}
        assert len(tournament_names) == 2
        assert "Aeroflot Open 2025" in tournament_names

    def test_tournament_location(self):
        g = self.games[0]
        assert g["tournament_location"] == "Moscow RUS"

    def test_tournament_dates(self):
        from datetime import date

        g = self.games[0]
        assert g["tournament_start_date"] == date(2025, 3, 1)
        assert g["tournament_end_date"] == date(2025, 3, 6)

    def test_opponent_title(self):
        # Novozhilov has title "f" (FM)
        assert self.games[0]["opponent_title"] == "f"

    def test_opponent_women_title(self):
        # Second tournament, first game: Tabermakova has women_title "wf"
        game_8 = self.games[8]
        assert game_8["opponent_name"] == "Tabermakova, Leila"
        assert game_8["opponent_women_title"] == "wf"

    def test_loss_result(self):
        # Korobkov game: result 0.00 → "0"
        korobkov = [g for g in self.games if "Korobkov" in (g["opponent_name"] or "")]
        assert len(korobkov) == 1
        assert korobkov[0]["result"] == "0"

    def test_win_result(self):
        # Sek, Konstantin: result 1.00 → "1"
        sek = [g for g in self.games if "Sek, Konstantin" in (g["opponent_name"] or "")]
        assert len(sek) == 1
        assert sek[0]["result"] == "1"

    def test_rating_with_star_marker(self):
        # Tabermakova has a blue * marker (>400 rating diff) — should still parse correctly
        tabermakova = [g for g in self.games if "Tabermakova" in (g["opponent_name"] or "")]
        assert len(tabermakova) == 1
        assert tabermakova[0]["opponent_rating"] == 2108


class TestEmptyInput:
    def test_empty_string(self):
        games, k, rating = parse_calculations("", 12345, "2025-01-01")
        assert games == []
        assert k is None
        assert rating is None

    def test_none_input(self):
        games, k, rating = parse_calculations(None, 12345, "2025-01-01")
        assert games == []
        assert k is None
        assert rating is None

    def test_no_tables(self):
        games, k, rating = parse_calculations("<div>No data</div>", 12345, "2025-01-01")
        assert games == []
        assert k is None
        assert rating is None
