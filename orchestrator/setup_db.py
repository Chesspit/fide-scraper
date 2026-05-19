"""Create and migrate the orchestrator SQLite database."""

import os
import sqlite3
from pathlib import Path

_DATA_DIR = Path(os.getenv("ORCHESTRATOR_DATA_DIR", Path(__file__).resolve().parent))
DB_PATH = _DATA_DIR / "scraper.db"


def create_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _apply_schema(conn)
    return conn


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scrape_groups (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            federation    TEXT    NOT NULL,
            continent     TEXT    NOT NULL,
            year          INTEGER NOT NULL,
            elo_min       INTEGER NOT NULL,
            elo_max       INTEGER NOT NULL,
            player_count  INTEGER NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'pending',
            priority      INTEGER NOT NULL,
            retries       INTEGER NOT NULL DEFAULT 0,
            last_run_at   TEXT,
            records_found INTEGER,
            notes         TEXT,
            UNIQUE(federation, year, elo_min)
        );

        CREATE INDEX IF NOT EXISTS idx_groups_status_priority
            ON scrape_groups(status, priority);
        CREATE INDEX IF NOT EXISTS idx_groups_fed_year_status
            ON scrape_groups(federation, year, status);

        CREATE TABLE IF NOT EXISTS scrape_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id      INTEGER NOT NULL REFERENCES scrape_groups(id),
            started_at    TEXT    NOT NULL,
            finished_at   TEXT,
            status        TEXT,
            records_found INTEGER,
            error_msg     TEXT,
            proxy_used    TEXT,
            profile_used  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_group
            ON scrape_runs(group_id);
    """)
    # Migrations for existing DBs
    for stmt in [
        "ALTER TABLE scrape_runs ADD COLUMN profile_used TEXT",
        "ALTER TABLE scrape_groups ADD COLUMN device TEXT",   # NULL = any device
        "ALTER TABLE scrape_groups ADD COLUMN profile TEXT",  # NULL = fuzzy selection
        "ALTER TABLE scrape_runs ADD COLUMN mb_downloaded REAL",  # MB pro Gruppe
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # column already exists


if __name__ == "__main__":
    conn = create_db()
    conn.close()
    print(f"DB created: {DB_PATH}")
