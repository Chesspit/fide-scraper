"""Dünne psycopg2-Hülle — gemeinsame Verbindungsoptionen für alle Blöcke.

Bewusst ohne pandas: das Worker-Image installiert es nicht. Retry-Logik bleibt
beim Aufrufer (scraper.db.ensure_connection, orchestrator.setup_db.connect),
weil die Anforderungen je Block verschieden sind.
"""

import psycopg2
import psycopg2.extras

from core.config import get_database_url


def connect(dsn: str | None = None, *, role: str | None = None,
            statement_timeout_ms: int | None = None,
            search_path: str | None = None,
            connect_timeout: int | None = None,
            readonly: bool = False,
            autocommit: bool = False):
    """psycopg2-Verbindung mit optionalem statement_timeout/search_path.

    dsn hat Vorrang; sonst core.config.get_database_url(role).
    """
    opts = []
    if statement_timeout_ms is not None:
        opts.append(f"-c statement_timeout={int(statement_timeout_ms)}")
    if search_path:
        opts.append(f"-c search_path={search_path}")
    kwargs = {}
    if opts:
        kwargs["options"] = " ".join(opts)
    if connect_timeout is not None:
        kwargs["connect_timeout"] = connect_timeout
    conn = psycopg2.connect(dsn or get_database_url(role), **kwargs)
    if readonly:
        conn.set_session(readonly=True)
    if autocommit:
        conn.autocommit = True
    return conn


def fetch_dicts(conn, sql: str, params=None) -> list[dict]:
    """Ergebniszeilen als Dicts (RealDictCursor)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_rows(conn, sql: str, params=None) -> tuple[list[str], list[tuple]]:
    """(Spaltennamen, Zeilen) — Grundlage für DataFrames beim Aufrufer."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()
