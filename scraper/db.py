import logging
from datetime import date

import psycopg2
import psycopg2.extras

from scraper.config import get_database_url

logger = logging.getLogger(__name__)


def is_valid_fide_period(d: date) -> bool:
    """Return True only for months FIDE actually published rating data.

    FIDE publishing history (verified against scrape_periods):
    - before 2008-04: no individual game data
    - 2008:           quarterly    → Apr, Jul, Oct
    - 2009:           5×/year      → Jan, Apr, Jul, Sep, Nov
    - 2010-01–2012-07: bi-monthly  → Jan, Mar, May, Jul, Sep, Nov
    - 2012-08+:        monthly     → all months
    """
    if d < date(2008, 4, 1):
        return False
    if d.year == 2008:
        return d.month in (4, 7, 10)
    if d.year == 2009:
        return d.month in (1, 4, 7, 9, 11)
    if d < date(2012, 8, 1):
        return d.month in (1, 3, 5, 7, 9, 11)
    return True


_FIDE_2024_CORRECTION_PERIOD = "2024-03-01"


def _maybe_save_fide_2024_correction(
    cur, fide_id: int, own_rating: int, games: list[dict]
):
    """Insert FIDE March 2024 one-off correction if player was below 2000 post-game.

    Formula: +0.4 × (2000 − post_game_rating).
    Uses ON CONFLICT DO NOTHING so existing snapshot_delta entries are not overwritten.
    """
    if own_rating is None:
        return
    game_change = sum(float(g.get("rating_change_weighted") or 0) for g in games)
    post_game = own_rating + game_change
    if post_game >= 2000:
        return
    amount = round(0.4 * (2000 - post_game))
    if amount <= 0:
        return
    cur.execute(
        """
        INSERT INTO rating_corrections (fide_id, period, amount, corr_type, source)
        VALUES (%s, '2024-03-01', %s, 'fide_one_off', 'formula')
        ON CONFLICT DO NOTHING
        """,
        (fide_id, amount),
    )


def _derive_no_data_reason(cur, fide_id: int, period_str: str) -> str:
    """Derive the reason a period has no data for the given player."""
    d = date.fromisoformat(period_str)
    if not is_valid_fide_period(d):
        return "system_gap"
    cur.execute("SELECT birth_year FROM players WHERE fide_id = %s", (fide_id,))
    row = cur.fetchone()
    if row and row[0] and d.year - row[0] < 10:
        return "too_young"
    return "inactive"


# 15 min cap. Everything legitimate (INSERTs, get_pending_periods cross-join,
# rating_history full scan) completes in seconds; this is a safety net against
# runaway queries like the 2026-04-20 ANY(huge_array) incident.
_STATEMENT_TIMEOUT_MS = 15 * 60 * 1000


def get_connection():
    return psycopg2.connect(
        get_database_url(),
        options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
    )


def _is_connection_broken(conn) -> bool:
    """True if the connection is closed or in an unrecoverable state."""
    if conn is None or conn.closed:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return False
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        return True


def ensure_connection(conn):
    """Return a live connection, reopening if broken.

    Retries up to 10× with increasing waits (5s, 10s, 20s, ...) to survive
    SSH tunnel drops — the tunnel auto-reconnects in ~5s, so a short wait
    is usually enough.
    """
    if not _is_connection_broken(conn):
        return conn
    logger.warning("DB connection broken; will retry to reconnect")
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass

    import time
    wait = 5
    for attempt in range(1, 11):
        try:
            new_conn = get_connection()
            logger.info("DB reconnected after %d attempt(s)", attempt)
            return new_conn
        except Exception as e:
            logger.warning("Reconnect attempt %d/10 failed (%s); waiting %ds", attempt, e, wait)
            time.sleep(wait)
            wait = min(wait * 2, 60)

    raise RuntimeError("Could not reconnect to DB after 10 attempts")


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
    cur,
    fide_id: int,
    period: str,
    status: str = "ok",
    k_factor: int | None = None,
    http_status: int | None = None,
    no_data_reason: str | None = None,
):
    cur.execute(
        """
        INSERT INTO scrape_periods (fide_id, period, status, k_factor, http_status, no_data_reason)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (fide_id, period)
        DO UPDATE SET scraped_at = NOW(), status = EXCLUDED.status,
                      k_factor = EXCLUDED.k_factor,
                      http_status = EXCLUDED.http_status,
                      no_data_reason = EXCLUDED.no_data_reason
        """,
        (fide_id, period, status, k_factor, http_status, no_data_reason),
    )


def _do_save_period(
    conn,
    fide_id: int,
    period: str,
    games: list[dict],
    k_factor: int | None,
    own_rating: int | None,
):
    with conn:
        with conn.cursor() as cur:
            inserted = upsert_games(cur, games)
            upsert_rating_history(cur, fide_id, period, own_rating)
            mark_period_scraped(cur, fide_id, period, "ok", k_factor)
            if period == _FIDE_2024_CORRECTION_PERIOD:
                _maybe_save_fide_2024_correction(cur, fide_id, own_rating, games)
    return inserted


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
    On connection loss: reopen once and retry.
    On other error: rollback games/rating, still mark scrape_periods(status='error').
    Returns the (possibly reopened) connection — callers should reassign.
    """
    try:
        inserted = _do_save_period(
            conn, fide_id, period, games, k_factor, own_rating
        )
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
        logger.warning(
            "Connection lost saving fide_id=%s period=%s (%s); reconnecting and retrying",
            fide_id, period, exc.__class__.__name__,
        )
        conn = ensure_connection(conn)
        inserted = _do_save_period(
            conn, fide_id, period, games, k_factor, own_rating
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception(
            "Error saving fide_id=%s period=%s — marking as error", fide_id, period
        )
        try:
            conn = ensure_connection(conn)
            with conn:
                with conn.cursor() as cur:
                    mark_period_scraped(cur, fide_id, period, "error", k_factor)
        except Exception:
            logger.exception("Could not mark period as error")
        return conn

    logger.info(
        "Saved fide_id=%s period=%s: %d games, K=%s, Ro=%s",
        fide_id, period, inserted, k_factor, own_rating,
    )
    return conn


def save_period_no_data(conn, fide_id: int, period: str, http_status: int | None = None):
    """Mark a period as having no data (empty calculations page).

    Returns the (possibly reopened) connection — callers should reassign.
    """
    try:
        with conn:
            with conn.cursor() as cur:
                reason = _derive_no_data_reason(cur, fide_id, period)
                mark_period_scraped(cur, fide_id, period, "no_data",
                                    http_status=http_status, no_data_reason=reason)
        return conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
        logger.warning(
            "Connection lost marking no_data fide_id=%s period=%s (%s); reconnecting",
            fide_id, period, exc.__class__.__name__,
        )
        conn = ensure_connection(conn)
        with conn:
            with conn.cursor() as cur:
                reason = _derive_no_data_reason(cur, fide_id, period)
                mark_period_scraped(cur, fide_id, period, "no_data",
                                    http_status=http_status, no_data_reason=reason)
        return conn


def get_fide_ids_for_groups(conn, groups: list[str]) -> list[int]:
    """Return fide_ids for the given group names.

    Resolution order per group name:
    1. 'swiss_2026' → players.swiss_2026 = TRUE
    2. players.analysis_group = group_name (seeded groups)
    3. groups table fallback: elo_min/elo_max/federations filter (full_population groups
       that haven't been seeded yet)
    """
    fide_ids: set[int] = set()

    analysis_groups = [g for g in groups if g != "swiss_2026"]
    include_swiss   = "swiss_2026" in groups

    with conn.cursor() as cur:
        # 1+2: swiss flag + analysis_group
        cur.execute(
            """
            SELECT fide_id FROM players
            WHERE (%s AND analysis_group = ANY(%s))
               OR (%s AND swiss_2026 = TRUE)
            """,
            (bool(analysis_groups), analysis_groups or [""], include_swiss),
        )
        fide_ids.update(row[0] for row in cur.fetchall())

        # 3: fallback via groups table for groups not yet seeded
        if analysis_groups:
            cur.execute(
                """
                SELECT g.group_name, g.elo_min, g.elo_max, g.federations
                FROM groups g
                WHERE g.group_name = ANY(%s)
                  AND g.sampling = 'full_population'
                  AND g.elo_min IS NOT NULL
                  AND g.elo_max IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM players p
                      WHERE p.analysis_group = g.group_name
                      LIMIT 1
                  )
                """,
                (analysis_groups,),
            )
            unseeded = cur.fetchall()

        for group_name, elo_min, elo_max, federations in (unseeded if analysis_groups else []):
            feds = [f.strip() for f in federations.split(",")] if federations else None
            if feds:
                cur.execute(
                    """
                    SELECT fide_id FROM players
                    WHERE active = TRUE
                      AND std_rating BETWEEN %s AND %s
                      AND federation = ANY(%s)
                    """,
                    (elo_min, elo_max, feds),
                )
            else:
                cur.execute(
                    """
                    SELECT fide_id FROM players
                    WHERE active = TRUE
                      AND std_rating BETWEEN %s AND %s
                    """,
                    (elo_min, elo_max),
                )
            new_ids = [row[0] for row in cur.fetchall()]
            logger.info(
                "Group '%s' not seeded — resolved %d players via groups table (ELO %s–%s, fed=%s)",
                group_name, len(new_ids), elo_min, elo_max, federations or "all",
            )
            fide_ids.update(new_ids)

    return sorted(fide_ids)


def get_pending_periods(
    conn,
    periods: list[str],
    fide_ids: list[int] | None = None,
    groups: list[str] | None = None,
) -> list[tuple[int, str]]:
    """Return (fide_id, period) pairs not yet in scrape_periods.

    Priority: fide_ids > groups > all analysis players.
    groups accepts analysis_group values plus 'swiss_2026'.
    """
    if groups and not fide_ids:
        fide_ids = get_fide_ids_for_groups(conn, groups)

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
                WHERE (p.analysis_group IS NOT NULL OR p.swiss_2026 = TRUE)
                  AND NOT EXISTS (
                    SELECT 1 FROM scrape_periods sp
                    WHERE sp.fide_id = p.fide_id AND sp.period = per.period
                )
                ORDER BY per.period, p.fide_id
                """,
                (periods,),
            )
        return cur.fetchall()
