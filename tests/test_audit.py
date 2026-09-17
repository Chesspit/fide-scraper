"""Tests für orchestrator/audit.py — Datenprüfung ab Stichtag.

Braucht die PG-Test-DB (conftest.py::data_db); ohne erreichbare PG: skip.
Migration 018 wird aus der Datei eingespielt (wie test_elo_bands.py mit 017),
damit SQL- und Python-Erwartungswert gegeneinander geprüft werden.

Szenario-Periode 2024-05 (Vorliste 2024-04, Kappung 400 aktiv).
"""

from datetime import date
from pathlib import Path

import pytest

from orchestrator.audit import (
    HARD,
    INFO,
    OK,
    SOFT,
    audit_periods,
    diff_cap,
    fide_expected,
    parse_period,
    previous_period,
    run_audit,
)
from orchestrator.integrity import check_ok_without_games, check_orphan_games
from orchestrator.setup_db import connect

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "018_fide_expected_score.sql"

P = "2024-05-01"
PREV = "2024-04-01"


@pytest.fixture
def conn(data_db):
    c = connect()
    with c.cursor() as cur:
        cur.execute(MIGRATION.read_text())
    yield c
    c.close()


def _audit(**kw):
    kw.setdefault("since", date(2024, 5, 1))
    kw.setdefault("until", date(2024, 5, 1))
    kw.setdefault("jobs", 1)
    return run_audit(connect, **kw)


def _green_player(db, fide_id, fed="GER"):
    """Liste 2 Partien, gescrapt, Formel + Kette stimmen."""
    db.insert_player(fide_id, federation=fed)
    db.insert_rating(fide_id, PREV, published_rating=2000, num_games=0)
    # Ro 2000: gegen 2100 gewonnen (E=0.36 → +0.64), gegen 2000 remis (0)
    db.insert_rating(fide_id, P, published_rating=2013, num_games=2, std_rating=2000)
    db.insert_period(fide_id, P, status="ok", k_factor=20)
    db.insert_game(fide_id, P, 1, 12.8, result="1", rating_change=0.64,
                   opponent_rating=2100, tournament_name="Open A")
    db.insert_game(fide_id, P, 2, 0.0, result="0.5", rating_change=0.0,
                   opponent_rating=2000, tournament_name="Open A")


# ── Elo-Formel: Python ↔ SQL ─────────────────────────────────────────────────

class TestExpectedScore:
    @pytest.mark.parametrize("diff,expected", [
        (0, 0.50), (3, 0.50), (4, 0.51), (25, 0.53), (26, 0.54), (95, 0.63),
        (-95, 0.37), (392, 0.92), (400, 0.92), (735, 0.99), (736, 1.00),
        (-736, 0.00),
    ])
    def test_python_table(self, diff, expected):
        assert fide_expected(diff) == expected

    def test_sql_matches_python(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT d, fn_fide_expected(d) FROM generate_series(-1000, 1000) d")
            rows = cur.fetchall()
        mismatches = [(d, float(e)) for d, e in rows if float(e) != fide_expected(d)]
        assert mismatches == []

    def test_diff_cap_rule(self, conn):
        cases = [date(2021, 12, 31), date(2022, 1, 1), date(2024, 2, 29), date(2024, 3, 1)]
        with conn.cursor() as cur:
            for d in cases:
                cur.execute("SELECT fn_fide_diff_cap(%s)", (d,))
                assert cur.fetchone()[0] == diff_cap(d)
        assert [diff_cap(d) for d in cases] == [400, None, None, 400]


# ── Perioden ──────────────────────────────────────────────────────────────────

class TestPeriods:
    def test_parse(self):
        assert parse_period("2020-01") == date(2020, 1, 1)
        assert parse_period("2020-01-15") == date(2020, 1, 1)

    def test_previous_period_monthly_and_bimonthly(self):
        assert previous_period(date(2020, 1, 1)) == date(2019, 12, 1)
        assert previous_period(date(2012, 8, 1)) == date(2012, 7, 1)
        assert previous_period(date(2011, 3, 1)) == date(2011, 1, 1)
        assert previous_period(date(2010, 1, 1)) == date(2009, 11, 1)

    def test_audit_periods_only_valid_ones(self):
        ps = audit_periods(date(2011, 1, 1), date(2011, 12, 1))
        assert [p.month for p in ps] == [1, 3, 5, 7, 9, 11]

    def test_audit_periods_capped_at_previous_month(self):
        ps = audit_periods(date(date.today().year, 1, 1))
        assert all(p < date.today().replace(day=1) for p in ps)


# ── Szenario ──────────────────────────────────────────────────────────────────

class TestRunAudit:
    def test_all_green(self, data_db, conn):
        _green_player(data_db, 1)
        res = _audit()
        assert not res.has_hard()
        h = res.headline
        assert h["soll_combos"] == 1
        assert h["pct_periods_complete"] == 100.0
        assert h["pct_games_found"] == 100.0
        assert h["pct_chain_ok"] == 100.0
        assert res.check("game_formula").n_checked == 2
        assert res.check("game_formula").n_findings == 0
        assert res.check("players_never_scraped").severity == OK

    def test_missing_period_and_never_scraped_player(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.insert_player(2, active=False)
        data_db.insert_rating(2, P, published_rating=1800, num_games=5)
        res = _audit()
        assert res.has_hard()
        pm = res.check("periods_missing")
        assert (pm.n_findings, pm.n_checked, pm.severity) == (1, 2, HARD)
        ns = res.check("players_never_scraped")
        assert ns.n_findings == 1 and ns.sample[0]["fide_id"] == 2
        assert "inaktiv: 1" in ns.note
        assert res.headline["pct_games_found"] == round(100 * 2 / 7, 2)

    def test_listed_but_no_data(self, data_db, conn):
        data_db.insert_player(3)
        data_db.insert_rating(3, PREV, published_rating=1790, num_games=0)
        data_db.insert_rating(3, P, published_rating=1800, num_games=4)
        data_db.insert_period(3, P, status="no_data")
        res = _audit()
        assert res.check("periods_listed_no_data").n_findings == 1
        assert res.check("periods_listed_no_data").severity == HARD  # 100 % > 1 %

    def test_isolated_no_data_below_threshold_is_soft(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.insert_player(3)
        data_db.insert_rating(3, PREV, published_rating=1790, num_games=0)
        data_db.insert_rating(3, P, published_rating=1800, num_games=4)
        data_db.insert_period(3, P, status="no_data")
        res = _audit(no_data_threshold_pct=60.0)  # 1 von 2 versuchten = 50 %
        assert res.check("periods_listed_no_data").severity == SOFT

    def test_no_data_on_first_rating_is_info(self, data_db, conn):
        data_db.insert_player(3)
        data_db.insert_rating(3, P, published_rating=1800, num_games=4)  # keine Vorliste
        data_db.insert_period(3, P, status="no_data")
        res = _audit()
        assert res.check("periods_listed_no_data").n_findings == 0
        c = res.check("periods_no_data_first_rating")
        assert (c.n_findings, c.severity) == (1, INFO)

    def test_fewer_games_than_listed(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.rating_history SET num_games = 3 "
                        "WHERE fide_id = 1 AND period = %s", (P,))
        res = _audit()
        c = res.check("game_count_fewer")
        assert (c.n_findings, c.severity) == (1, HARD)
        assert c.worst_periods == [("2024-05-01", 100.0)]
        assert _audit(fewer_threshold_pct=100.0).check("game_count_fewer").severity == SOFT
        assert c.sample[0]["games"] == 2 and c.sample[0]["num_games"] == 3

    def test_fewer_games_on_first_rating_is_info(self, data_db, conn):
        data_db.insert_player(4)
        data_db.insert_rating(4, P, published_rating=1500, num_games=7)  # keine Vorliste
        data_db.insert_period(4, P, status="ok")
        data_db.insert_game(4, P, 1, 0.0)
        res = _audit()
        assert res.check("game_count_first_rating").severity == INFO
        assert res.check("game_count_fewer").n_findings == 0

    def test_formula_mismatch_detected(self, data_db, conn):
        _green_player(data_db, 1)
        # Spaltenverschiebung simulieren: Δ gehört nicht zum Rating-Paar
        data_db.execute("UPDATE public.game_results SET rating_change = 0.50, "
                        "rating_change_weighted = 10.0 WHERE fide_id = 1 AND game_index = 1")
        res = _audit()
        gf = res.check("game_formula")
        assert gf.n_findings == 1
        assert gf.severity == HARD  # 50 % > Schwelle
        assert gf.sample[0]["expected"] == 0.36

    def test_formula_ignores_multi_tournament_periods(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.game_results SET tournament_name = 'Open B', "
                        "rating_change = 0.10 WHERE fide_id = 1 AND game_index = 2")
        res = _audit()
        assert res.check("game_formula").n_checked == 0

    def test_games_of_unrated_player_are_info(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.insert_player(8)  # keine Listenzeile, weder jetzt noch vorher
        data_db.insert_period(8, P, status="ok")
        data_db.insert_game(8, P, 1, 0.0)
        res = _audit()
        assert res.check("games_unrated_players").n_findings == 1
        assert res.check("games_unrated_players").severity == INFO
        assert res.check("games_not_in_list").n_findings == 0

    def test_games_without_listing_of_rated_player_are_soft(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.insert_player(9)
        data_db.insert_rating(9, PREV, published_rating=1700, num_games=0)
        data_db.insert_rating(9, P, published_rating=1700, num_games=0)
        data_db.insert_period(9, P, status="ok")
        data_db.insert_game(9, P, 1, 0.0)
        res = _audit()
        assert res.check("games_not_in_list").severity == SOFT

    def test_cap_follows_tournament_start(self, data_db, conn):
        # Periode 2024-05 (gekappt), Turnier begann 2024-02 (ungekappt): Δ = 500 → E = 0.96
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.game_results SET opponent_rating = 1500, "
                        "rating_change = 0.04, rating_change_weighted = 0.8, "
                        "tournament_start_date = '2024-02-25' "
                        "WHERE fide_id = 1 AND game_index = 1")
        data_db.execute("UPDATE public.game_results SET tournament_start_date = '2024-02-25' "
                        "WHERE fide_id = 1 AND game_index = 2")
        assert _audit().check("game_formula").n_findings == 0
        data_db.execute("UPDATE public.game_results SET tournament_start_date = '2024-04-25' "
                        "WHERE fide_id = 1")
        assert _audit().check("game_formula").n_findings == 1  # gekappt wäre E = 0.92

    def test_k_reduced_by_700_rule_is_valid(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.scrape_periods SET k_factor = 38 WHERE fide_id = 1")
        data_db.execute("UPDATE public.game_results SET rating_change_weighted = 24.32 "
                        "WHERE fide_id = 1 AND game_index = 1")
        res = _audit()
        assert res.check("value_ranges").n_findings == 0
        assert res.check("k_times_change").n_findings == 0

    def test_k_differing_per_tournament_is_valid(self, data_db, conn):
        _green_player(data_db, 1)
        # Periode sagt K=20, diese Partie wurde mit K=40 gerechnet
        data_db.execute("UPDATE public.game_results SET rating_change_weighted = 25.6 "
                        "WHERE fide_id = 1 AND game_index = 1")
        assert _audit().check("k_times_change").n_findings == 0

    def test_k_times_change_mismatch(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.game_results SET rating_change_weighted = 6.5 "
                        "WHERE fide_id = 1 AND game_index = 1")  # 6.5 / 0.64 ist kein K
        res = _audit()
        assert res.check("k_times_change").n_findings == 1

    def test_value_ranges(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.game_results SET result = '2' "
                        "WHERE fide_id = 1 AND game_index = 2")
        res = _audit()
        vr = res.check("value_ranges")
        assert (vr.n_findings, vr.severity) == (1, HARD)
        assert vr.sample[0]["category"] == "bad_result"

    def test_chain_unexplained_vs_explained(self, data_db, conn):
        _green_player(data_db, 1)
        # Spieler 5: Kette bricht um 10 Punkte, Ro = Vorliste, K=20 → unerklärt
        data_db.insert_player(5)
        data_db.insert_rating(5, PREV, published_rating=1900, num_games=0)
        data_db.insert_rating(5, P, published_rating=1910, num_games=1, std_rating=1900)
        data_db.insert_period(5, P, status="ok", k_factor=20)
        data_db.insert_game(5, P, 1, 0.0, result="0.5", rating_change=0.0,
                            opponent_rating=1900)
        # Spieler 6: gleicher Bruch, aber K=40 → erklärt
        data_db.insert_player(6)
        data_db.insert_rating(6, PREV, published_rating=1600, num_games=0)
        data_db.insert_rating(6, P, published_rating=1610, num_games=1, std_rating=1600)
        data_db.insert_period(6, P, status="ok", k_factor=40)
        data_db.insert_game(6, P, 1, 0.0, result="0.5", rating_change=0.0,
                            opponent_rating=1600)
        res = _audit(chain_threshold_pct=50.0)
        ch = res.check("chain_matches_list")
        assert (ch.n_checked, ch.n_findings) == (3, 2)
        assert ch.severity == SOFT  # 33 % unerklärt < 50 %
        assert {s["category"] for s in ch.sample} == {"unexplained", "k40"}
        assert res.by_year[2024]["chain_unexplained"] == 1
        assert _audit(chain_threshold_pct=2.0).check("chain_matches_list").severity == HARD

    def test_known_correction_explains_chain(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.rating_history SET published_rating = 2063 "
                        "WHERE fide_id = 1 AND period = %s", (P,))
        data_db.execute("INSERT INTO public.rating_corrections (fide_id, period, amount) "
                        "VALUES (1, %s, 50)", (P,))
        res = _audit()
        assert res.check("chain_matches_list").n_findings == 0
        assert res.check("rating_jumps").n_findings == 0  # 63 < 150

    def test_approximated_march_2024_correction_uses_post_game_rating(self, data_db, conn):
        # Vorliste 1800, Partien +12 → 1812; FIDE: +0,4 × (2000 − 1812) = +75 → 1887.
        # Gespeichert ist die Ro-Näherung 0,4 × (2000 − 1800) = 80 (falsch für Spieler mit Partien).
        p, prev = "2024-03-01", "2024-02-01"
        data_db.insert_player(7)
        data_db.insert_rating(7, prev, published_rating=1800, num_games=0)
        data_db.insert_rating(7, p, published_rating=1887, num_games=1, std_rating=1800)
        data_db.insert_period(7, p, status="ok", k_factor=20)
        data_db.insert_game(7, p, 1, 12.0, result="1", rating_change=0.60,
                            opponent_rating=1870)
        data_db.execute("INSERT INTO public.rating_corrections (fide_id, period, amount, source) "
                        "VALUES (7, %s, 80, 'formula')", (p,))
        res = _audit(since=date(2024, 3, 1), until=date(2024, 3, 1))
        assert res.check("chain_matches_list").n_findings == 0

    def test_shifted_list_detected_and_skipped(self, data_db, conn):
        _green_player(data_db, 1)
        # Liste 2024-05 ist faktisch die Juni-Liste: gleiches Rating, gleiche Partienzahl
        data_db.insert_rating(1, "2024-06-01", published_rating=2013, num_games=2)
        res = _audit()
        c = res.check("ref_list_not_shifted")
        assert (c.n_findings, c.severity) == (1, HARD)
        assert res.skipped_periods == [date(2024, 5, 1)]

    def test_period_after_shifted_list_skips_chain(self, data_db, conn):
        # 2024-04 ist verwechselt (= Mai-Liste), 2024-05 wird geprüft, aber ohne Kette/Ro
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.rating_history SET published_rating = 2013, num_games = 2 "
                        "WHERE fide_id = 1 AND period = %s", (PREV,))
        res = _audit(since=date(2024, 4, 1))
        assert res.skipped_periods == [date(2024, 4, 1)]
        assert res.check("chain_matches_list").n_checked == 0
        assert res.check("ro_matches_list").n_checked == 0
        assert "2024-05" in res.check("chain_matches_list").note
        assert res.check("periods_missing").n_checked == 1  # 2024-05 selbst zählt

    def test_ro_shift_is_info(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.execute("UPDATE public.rating_history SET std_rating = 1990 "
                        "WHERE fide_id = 1 AND period = %s", (P,))
        res = _audit()
        assert res.check("ro_matches_list").severity == INFO
        assert res.check("ro_matches_list").n_findings == 1

    def test_missing_reference_list_skips_period(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, P, status="ok")
        res = _audit()
        assert res.check("ref_lists_present").severity == HARD
        assert res.skipped_periods == [date(2024, 5, 1)]
        assert res.headline["soll_combos"] == 0  # nicht fälschlich "vollständig"

    def test_federation_filter(self, data_db, conn):
        _green_player(data_db, 1, fed="GER")
        data_db.insert_player(2, federation="AUT")
        data_db.insert_rating(2, P, published_rating=1800, num_games=5)
        res = _audit(federations=["GER"])
        assert not res.check("periods_missing").n_findings
        assert _audit(federations=["AUT"]).check("periods_missing").n_findings == 1

    def test_integrity_checks_respect_period_range(self, data_db, conn):
        _green_player(data_db, 1)
        data_db.insert_period(1, "2023-01-01", status="ok")      # ok ohne Partien, außerhalb
        data_db.insert_game(1, "2023-02-01", 1, 1.0)             # verwaist, außerhalb
        res = _audit()
        assert res.check("integrity:ok_without_games").n_findings == 0
        assert res.check("integrity:orphan_games").n_findings == 0
        # Ohne Grenzen (bisheriges Verhalten) werden beide gefunden
        assert len(check_ok_without_games(conn)) == 1
        assert len(check_orphan_games(conn)) == 1
