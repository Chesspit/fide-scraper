"""Tests for scraper.db — uses a real PostgreSQL connection if available, otherwise skips."""

import os

import pytest

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

DB_URL = os.environ.get("DATABASE_URL", "")
requires_db = pytest.mark.skipif(
    not DB_URL or not HAS_PSYCOPG2,
    reason="DATABASE_URL not set or psycopg2 not installed"
)


@pytest.fixture
def db_conn():
    """Provide a DB connection that rolls back after each test."""
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def setup_tables(db_conn):
    """Ensure tables exist and insert a test player."""
    with db_conn.cursor() as cur:
        # Read and execute migration
        from pathlib import Path
        migration = Path(__file__).parent.parent / "migrations" / "001_initial.sql"
        cur.execute(migration.read_text())

        # Insert test player
        cur.execute("""
            INSERT INTO players (fide_id, name, federation, sex, std_rating, analysis_group)
            VALUES (99999999, 'Test Player', 'TST', 'M', 2500, 'male_control')
            ON CONFLICT DO NOTHING
        """)
    db_conn.commit()
    yield
    # Cleanup
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM game_results WHERE fide_id = 99999999")
        cur.execute("DELETE FROM rating_history WHERE fide_id = 99999999")
        cur.execute("DELETE FROM scrape_periods WHERE fide_id = 99999999")
        cur.execute("DELETE FROM players WHERE fide_id = 99999999")
    db_conn.commit()


@requires_db
class TestUpsertGames:
    def test_insert_games(self, db_conn, setup_tables):
        from scraper.db import upsert_games

        games = [
            {
                "fide_id": 99999999, "period": "2025-04-01",
                "opponent_name": "Opponent A", "opponent_title": "m",
                "opponent_women_title": None, "opponent_rating": 2400,
                "opponent_federation": "GER", "result": "1",
                "rating_change": 0.35, "rating_change_weighted": 3.5,
                "color": "W", "tournament_name": "Test Open",
                "tournament_location": "Berlin GER",
                "tournament_start_date": "2025-03-01",
                "tournament_end_date": "2025-03-05",
                "game_index": 0,
            },
            {
                "fide_id": 99999999, "period": "2025-04-01",
                "opponent_name": "Opponent B", "opponent_title": None,
                "opponent_women_title": "wg", "opponent_rating": 2350,
                "opponent_federation": "FRA", "result": "0.5",
                "rating_change": -0.2, "rating_change_weighted": -2.0,
                "color": "B", "tournament_name": "Test Open",
                "tournament_location": "Berlin GER",
                "tournament_start_date": "2025-03-01",
                "tournament_end_date": "2025-03-05",
                "game_index": 1,
            },
        ]

        with db_conn:
            with db_conn.cursor() as cur:
                inserted = upsert_games(cur, games)

        assert inserted == 2

    def test_duplicate_game_index_ignored(self, db_conn, setup_tables):
        from scraper.db import upsert_games

        game = {
            "fide_id": 99999999, "period": "2025-04-01",
            "opponent_name": "Opponent A", "opponent_title": None,
            "opponent_women_title": None, "opponent_rating": 2400,
            "opponent_federation": "GER", "result": "1",
            "rating_change": 0.35, "rating_change_weighted": 3.5,
            "color": "W", "tournament_name": "Test Open",
            "tournament_location": "Berlin GER",
            "tournament_start_date": "2025-03-01",
            "tournament_end_date": "2025-03-05",
            "game_index": 0,
        }

        with db_conn:
            with db_conn.cursor() as cur:
                upsert_games(cur, [game])
                # Insert same game_index again → should be ignored
                inserted = upsert_games(cur, [game])

        assert inserted == 0


@requires_db
class TestSavePeriod:
    def test_save_period_creates_all_records(self, db_conn, setup_tables):
        from scraper.db import save_period

        games = [
            {
                "fide_id": 99999999, "period": "2025-04-01",
                "opponent_name": "Opp", "opponent_title": None,
                "opponent_women_title": None, "opponent_rating": 2400,
                "opponent_federation": "GER", "result": "1",
                "rating_change": 0.35, "rating_change_weighted": 3.5,
                "color": "W", "tournament_name": "Test",
                "tournament_location": None,
                "tournament_start_date": None, "tournament_end_date": None,
                "game_index": 0,
            }
        ]

        save_period(db_conn, 99999999, "2025-04-01", games, 10, 2500)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM game_results WHERE fide_id = 99999999 AND period = '2025-04-01'"
            )
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT std_rating FROM rating_history WHERE fide_id = 99999999 AND period = '2025-04-01'"
            )
            assert cur.fetchone()[0] == 2500

            cur.execute(
                "SELECT status, k_factor FROM scrape_periods WHERE fide_id = 99999999 AND period = '2025-04-01'"
            )
            row = cur.fetchone()
            assert row[0] == "ok"
            assert row[1] == 10


@requires_db
class TestGetPendingPeriods:
    def test_returns_unscraped(self, db_conn, setup_tables):
        from scraper.db import get_pending_periods

        pending = get_pending_periods(db_conn, ["2025-04-01"], [99999999])
        assert len(pending) == 1
        assert pending[0][0] == 99999999

    def test_excludes_scraped(self, db_conn, setup_tables):
        from scraper.db import get_pending_periods, mark_period_scraped

        with db_conn:
            with db_conn.cursor() as cur:
                mark_period_scraped(cur, 99999999, "2025-04-01", "ok", 10)

        pending = get_pending_periods(db_conn, ["2025-04-01"], [99999999])
        assert len(pending) == 0
