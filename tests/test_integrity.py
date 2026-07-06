"""Tests für orchestrator/integrity.py — False-Positive-Erkennung.

Braucht die PG-Test-DB (conftest.py::data_db); ohne erreichbare PG: skip.
Jeder Check bekommt einen Widerspruchs-Fall + einen Gesund-Fall.
"""

import pytest

from orchestrator.integrity import (
    CHECK_IDS,
    check_blocked_error_rows,
    check_done_groups_missing_combos,
    check_no_data_with_games,
    check_ok_without_games,
    check_orphan_games,
    has_hard_findings,
    run_checks,
)
from orchestrator.setup_db import connect


@pytest.fixture
def conn(data_db):
    c = connect()
    yield c
    c.close()


class TestOkWithoutGames:
    def test_detects_ok_without_games(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="ok")  # keine Partien!
        findings = check_ok_without_games(conn)
        assert len(findings) == 1
        assert findings[0]["fide_id"] == 1
        assert findings[0]["severity"] == "soft"  # kein num_games-Beleg

    def test_hard_when_official_list_proves_games(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="ok")
        data_db.insert_rating(1, "2024-05-01", num_games=7)  # Liste sagt: 7 Partien
        findings = check_ok_without_games(conn)
        assert findings[0]["severity"] == "hard"
        assert findings[0]["num_games_official"] == 7

    def test_healthy_ok_with_games_passes(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="ok")
        data_db.insert_game(1, "2024-05-01")
        assert check_ok_without_games(conn) == []


class TestNoDataWithGames:
    def test_detects_contradiction(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="no_data")
        data_db.insert_game(1, "2024-05-01", game_index=1)
        data_db.insert_game(1, "2024-05-01", game_index=2)
        findings = check_no_data_with_games(conn)
        assert len(findings) == 1
        assert findings[0]["game_rows"] == 2
        assert findings[0]["severity"] == "hard"

    def test_healthy_no_data_passes(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="no_data")
        assert check_no_data_with_games(conn) == []


class TestBlockedErrorRows:
    def test_detects_error_and_http_blocked(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="error")
        data_db.insert_period(1, "2024-06-01", status="no_data", http_status=429)
        data_db.insert_period(1, "2024-07-01", status="no_data", http_status=403)
        data_db.insert_period(1, "2024-08-01", status="no_data")   # echtes no_data
        data_db.insert_period(1, "2024-09-01", status="ok")
        data_db.insert_game(1, "2024-09-01")
        findings = check_blocked_error_rows(conn)
        assert len(findings) == 3
        assert {f["period"].isoformat() for f in findings} == {
            "2024-05-01", "2024-06-01", "2024-07-01"
        }


class TestDoneGroupsMissingCombos:
    def _seed_group(self, data_db, n_players=3, scraped_players=None):
        """Gruppe GER/2024/2000-2099 mit n Spielern; scraped_players bekommen
        alle 12 Perioden des Jahres 2024 als ok."""
        gid = data_db.insert_group(federation="GER", year=2024,
                                   elo_min=2000, elo_max=2099, status="done")
        for i in range(1, n_players + 1):
            data_db.insert_player(i, federation="GER", std_rating=2050)
        for fid in (scraped_players or []):
            for m in range(1, 13):
                data_db.insert_period(fid, f"2024-{m:02d}-01", status="ok")
        return gid

    def test_fully_scraped_done_group_passes(self, data_db, conn):
        self._seed_group(data_db, n_players=2, scraped_players=[1, 2])
        assert check_done_groups_missing_combos(conn) == []

    def test_detects_missing_combos(self, data_db, conn):
        # 3 Spieler erwartet, nur 1 gescrapt → 2/3 der Kombos fehlen
        gid = self._seed_group(data_db, n_players=3, scraped_players=[1])
        findings = check_done_groups_missing_combos(conn)
        assert len(findings) == 1
        f = findings[0]
        assert f["group_id"] == gid
        assert f["missing_pct"] > 60
        assert f["severity"] == "hard"

    def test_small_drift_below_threshold_passes(self, data_db, conn):
        # 1 fehlende Periode von 24 (~4.2%) — Schwellwert hochsetzen → kein Finding
        self._seed_group(data_db, n_players=2, scraped_players=[1, 2])
        data_db.execute(
            "DELETE FROM public.scrape_periods WHERE fide_id=2 AND period='2024-12-01'"
        )
        assert check_done_groups_missing_combos(conn, threshold_pct=5.0) == []
        assert len(check_done_groups_missing_combos(conn, threshold_pct=2.0)) == 1

    def test_tier_sentinels_excluded(self, data_db, conn):
        data_db.insert_group(federation="P3", year=2026,
                             elo_min=0, elo_max=2299, status="done")
        assert check_done_groups_missing_combos(conn) == []

    def test_update_only_groups_excluded(self, data_db, conn):
        # update_only-Batches (alte dc_update-Ära): Soll-Menge war "bereits
        # gescrapte Spieler zum Claim-Zeitpunkt" — Audit gegen alle aktiven
        # Spieler wäre systematisch falsch-positiv (73 solcher Treffer im
        # ersten Live-Lauf 2026-07-06).
        data_db.insert_group(federation="GER", year=2024, elo_min=2000,
                             elo_max=2099, status="done", update_only=1)
        data_db.insert_player(1, federation="GER", std_rating=2050)  # nie gescrapt
        assert check_done_groups_missing_combos(conn) == []

    def test_pending_groups_not_audited(self, data_db, conn):
        self._seed_group(data_db, n_players=3, scraped_players=[])
        data_db.execute("UPDATE orchestrator.scrape_groups SET status='pending'")
        assert check_done_groups_missing_combos(conn) == []


class TestOrphanGames:
    def test_detects_untracked_games(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_game(1, "2024-05-01")  # keine scrape_periods-Zeile
        findings = check_orphan_games(conn)
        assert len(findings) == 1
        assert findings[0]["severity"] == "soft"

    def test_tracked_games_pass(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="ok")
        data_db.insert_game(1, "2024-05-01")
        assert check_orphan_games(conn) == []


class TestRunner:
    def test_run_all_checks_on_healthy_db(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="ok")
        data_db.insert_game(1, "2024-05-01")
        results = run_checks(conn)
        assert set(results) == set(CHECK_IDS)
        assert all(v == [] for v in results.values())
        assert not has_hard_findings(results)

    def test_check_selection_and_hard_flag(self, data_db, conn):
        data_db.insert_player(1)
        data_db.insert_period(1, "2024-05-01", status="error")
        results = run_checks(conn, check_ids=["blocked_error_rows"])
        assert list(results) == ["blocked_error_rows"]
        assert has_hard_findings(results)
