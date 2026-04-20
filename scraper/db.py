import logging

import psycopg2
import psycopg2.extras

from scraper.config import get_database_url

logger = logging.getLogger(__name__)


# 15 min cap. Everything legitimate (INSERTs, get_pending_periods cross-join,
# rating_history full scan) completes in seconds; this is a safety net against
# runaway queries like the 2026-04-20 ANY(huge_array) incident.
_STATEMENT_TIMEOUT_MS = 15 * 60 * 1000


def get_connection():
    return psycopg2.connect(
        get_database_url(),
        options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
    )


def upsert_games(cur, games: list[dict]) -> int:
    """Insert games into game_results. Returns number of rows inserted."""
    if not games:
        return 0

    sql = """
        INSERT INTO game_results (
            fide_id, period, opponent_name, opponent_title, opponent_women_title,
            opponent_rating, opponent_federation, result, rating_change,
            rating_change_weighted, color, tournament_name, tournament_location,
            tournament_start_date, tournament_end_date, game_index
        ) VALUES (
            %(fide_id)s, %(period)s, %(opponent_name)s, %(opponent_title)s,
            %(opponent_women_title)s, %(opponent_rating)s, %(opponent_federation)s,
            %(result)s, %(rating_change)s, %(rating_change_weighted)s, %(color)s,
            %(tournament_name)s, %(tournament_location)s, %(tournament_start_date)s,
            %(tournament_end_date)s, %(game_index)s
        )
        ON CONFLICT (fide_id, period, game_index) DO NOTHING
    """
    count = 0
    for game in games:
        cur.execute(sql, game)
        count += cur.rowcount
    return count


def upsert_rating_history(cur, fide_id: int, period: str, own_rating: int | None):
    """Insert or update the player's own rating for a period."""
    if own_rating is None:
        return
    cur.execute(
        """
        INSERT INTO rating_history (fide_id, period, std_rating)
        VALUES (%s, %s, %s)
        ON CONFLICT (fide_id, period)
        DO UPDATE SET std_rating = EXCLUDED.std_rating
        """,
        (fide_id, period, own_rating),
    )


def mark_period_scraped(
    cur, fide_id: int, period: str, status: str = "ok", k_factor: int | None = None
):
    cur.execute(
        """
        INSERT INTO scrape_periods (fide_id, period, status, k_factor)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (fide_id, period)
        DO UPDATE SET scraped_at = NOW(), status = EXCLUDED.status,
                      k_factor = EXCLUDED.k_factor
        """,
        (fide_id, period, status, k_factor),
    )


def save_period(
    conn,
    fide_id: int,
    period: str,
    games: list[dict],
    k_factor: int | None,
    own_rating: int | None,
):
    """Save all data for a (fide_id, period) in a single transaction.

    On success: games + rating_history + scrape_periods(status='ok').
    On error: rollback games/rating, still mark scrape_periods(status='error').
    """
    try:
        with conn:
            with conn.cursor() as cur:
                inserted = upsert_games(cur, games)
                upsert_rating_history(cur, fide_id, period, own_rating)
                mark_period_scraped(cur, fide_id, period, "ok", k_factor)
        logger.info(
            "Saved fide_id=%s period=%s: %d games, K=%s, Ro=%s",
            fide_id, period, inserted, k_factor, own_rating,
        )
    except Exception:
        conn.rollback()
        logger.exception(
            "Error saving fide_id=%s period=%s — marking as error", fide_id, period
        )
        try:
            with conn:
                with conn.cursor() as cur:
                    mark_period_scraped(cur, fide_id, period, "error", k_factor)
        except Exception:
            logger.exception("Could not mark period as error")
        raise


def save_period_no_data(conn, fide_id: int, period: str):
    """Mark a period as having no data (empty calculations page)."""
    with conn:
        with conn.cursor() as cur:
            mark_period_scraped(cur, fide_id, period, "no_data")


def get_pending_periods(
    conn, periods: list[str], fide_ids: list[int] | None = None
) -> list[tuple[int, str]]:
    """Return (fide_id, period) pairs not yet in scrape_periods.

    If fide_ids is None, uses all players with analysis_group IS NOT NULL.
    """
    with conn.cursor() as cur:
        if fide_ids:
            cur.execute(
                """
                SELECT p.fide_id, per.period
                FROM unnest(%s::integer[]) AS p(fide_id)
                CROSS JOIN unnest(%s::date[]) AS per(period)
                WHERE NOT EXISTS (
                    SELECT 1 FROM scrape_periods sp
                    WHERE sp.fide_id = p.fide_id AND sp.period = per.period
                )
                ORDER BY per.period, p.fide_id
                """,
                (fide_ids, periods),
            )
        else:
            cur.execute(
                """
                SELECT p.fide_id, per.period
                FROM players p
                CROSS JOIN unnest(%s::date[]) AS per(period)
                WHERE p.analysis_group IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM scrape_periods sp
                    WHERE sp.fide_id = p.fide_id AND sp.period = per.period
                )
                ORDER BY per.period, p.fide_id
                """,
                (periods,),
            )
        return cur.fetchall()
