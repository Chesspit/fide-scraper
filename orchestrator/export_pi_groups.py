"""Export Pi-assigned groups from the VPS orchestrator DB into a standalone scraper.db.

Usage (inside orchestrator-worker-1 container on VPS):
    python3 orchestrator/export_pi_groups.py [--out /tmp/scraper_pi.db]

The output file is a self-contained SQLite ready to be copied to the Pi.
All groups have status='pending', thread_affinity=NULL, device='raspi'.
"""

import argparse
import sqlite3
from pathlib import Path


def export(src: str, dst: str) -> None:
    src_conn = sqlite3.connect(src)
    src_conn.row_factory = sqlite3.Row

    groups = src_conn.execute(
        "SELECT * FROM scrape_groups WHERE device = 'raspi' ORDER BY priority"
    ).fetchall()

    if not groups:
        print("Keine Gruppen mit device='raspi' gefunden.")
        return

    dst_conn = sqlite3.connect(dst)
    dst_conn.execute("PRAGMA journal_mode=WAL")

    dst_conn.executescript("""
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
            device        TEXT,
            profile       TEXT,
            thread_affinity TEXT,
            update_only   INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_groups_status_priority
            ON scrape_groups(status, priority);
        CREATE TABLE IF NOT EXISTS scrape_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id      INTEGER NOT NULL REFERENCES scrape_groups(id),
            started_at    TEXT    NOT NULL,
            finished_at   TEXT,
            status        TEXT,
            records_found INTEGER,
            error_msg     TEXT,
            proxy_used    TEXT,
            profile_used  TEXT,
            mb_downloaded REAL,
            thread_slot   INTEGER
        );
    """)

    dst_conn.executemany(
        """INSERT INTO scrape_groups
           (federation, continent, year, elo_min, elo_max, player_count,
            status, priority, retries, notes, device, profile, thread_affinity, update_only)
           VALUES (?,?,?,?,?,?, 'pending',?,0, ?,?,?,NULL,?)""",
        [
            (
                g["federation"], g["continent"], g["year"],
                g["elo_min"], g["elo_max"], g["player_count"],
                g["priority"], g["notes"],
                g["device"], g["profile"], g["update_only"],
            )
            for g in groups
        ],
    )
    dst_conn.commit()

    print(f"Exportiert: {len(groups)} Gruppen → {dst}")
    cur = dst_conn.execute(
        "SELECT year, COUNT(*), SUM(player_count) FROM scrape_groups GROUP BY year ORDER BY year"
    )
    for row in cur:
        print(f"  {row[0]}: {row[1]} Gruppen, {row[2]} Spieler")

    src_conn.close()
    dst_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default="/data/scraper.db")
    parser.add_argument("--out", default="/tmp/scraper_pi.db")
    args = parser.parse_args()
    export(args.src, args.out)
