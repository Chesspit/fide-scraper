#!/usr/bin/env python3
"""Einmalige Datenübernahme der Orchestrator-Queue: SQLite scraper.db → PostgreSQL.

Review #5 (Architektur-Review 2026-07-03). Kopiert scrape_groups und
scrape_runs mit identischen IDs ins Schema "orchestrator" der fidedb
(Migration 013 / setup_db.ensure_schema) und setzt die Sequenzen auf
MAX(id). Verweigert den Lauf, wenn die Zieltabellen nicht leer sind
(--force überschreibt: TRUNCATE + Neuimport).

Ausführen im Worker-Container auf dem VPS (Worker vorher stoppen!):
    docker compose run --rm worker \
        python scripts/migrate_queue_to_pg.py --sqlite /data/scraper.db

Verifikation (Zeilenzahlen + Status-Verteilung) läuft automatisch am Ende.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.setup_db import connect

GROUP_COLS = [
    "id", "federation", "continent", "year", "elo_min", "elo_max",
    "player_count", "status", "priority", "retries", "last_run_at",
    "records_found", "notes", "device", "profile", "thread_affinity",
    "update_only",
]
RUN_COLS = [
    "id", "group_id", "started_at", "finished_at", "status", "records_found",
    "error_msg", "proxy_used", "profile_used", "mb_downloaded", "thread_slot",
]


def _clean_ts(value):
    """SQLite-Zeitstempel → PG-tauglich; leere Strings werden NULL."""
    if value in ("", None):
        return None
    return value


def _load_sqlite(db_path: Path) -> tuple[list[tuple], list[tuple]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    groups = [
        tuple(r[c] if c not in ("last_run_at",) else _clean_ts(r[c]) for c in GROUP_COLS)
        for r in conn.execute(f"SELECT {','.join(GROUP_COLS)} FROM scrape_groups")
    ]
    runs = [
        tuple(_clean_ts(r[c]) if c in ("started_at", "finished_at") else r[c] for c in RUN_COLS)
        for r in conn.execute(f"SELECT {','.join(RUN_COLS)} FROM scrape_runs")
    ]
    conn.close()
    return groups, runs


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue-Daten SQLite → PostgreSQL migrieren")
    parser.add_argument("--sqlite", type=Path, default=Path("/data/scraper.db"),
                        help="Pfad zur alten scraper.db (default: /data/scraper.db)")
    parser.add_argument("--force", action="store_true",
                        help="Zieltabellen vorher leeren (TRUNCATE) statt abzubrechen")
    args = parser.parse_args()

    if not args.sqlite.exists():
        print(f"FEHLER: SQLite-DB nicht gefunden: {args.sqlite}")
        return 1

    groups, runs = _load_sqlite(args.sqlite)
    print(f"SQLite: {len(groups):,} Gruppen, {len(runs):,} Runs geladen.")

    pg = connect()
    cur = pg.cursor()

    cur.execute("SELECT COUNT(*) FROM scrape_groups")
    existing = cur.fetchone()[0]
    if existing:
        if not args.force:
            print(f"ABBRUCH: orchestrator.scrape_groups enthält bereits {existing:,} Zeilen "
                  f"(--force zum Überschreiben).")
            return 1
        print(f"--force: leere Zieltabellen ({existing:,} Gruppen).")
        cur.execute("TRUNCATE scrape_runs, scrape_groups RESTART IDENTITY CASCADE")

    # Import in EINER Transaktion — halbmigrierter Zustand ist schlimmer als keiner.
    pg.autocommit = False
    try:
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO scrape_groups ({','.join(GROUP_COLS)}) VALUES %s",
            groups, page_size=1000,
        )
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO scrape_runs ({','.join(RUN_COLS)}) VALUES %s",
            runs, page_size=1000,
        )
        # Sequenzen hinter die übernommenen IDs setzen
        cur.execute("SELECT setval(pg_get_serial_sequence('scrape_groups','id'), "
                    "COALESCE((SELECT MAX(id) FROM scrape_groups), 1))")
        cur.execute("SELECT setval(pg_get_serial_sequence('scrape_runs','id'), "
                    "COALESCE((SELECT MAX(id) FROM scrape_runs), 1))")
        pg.commit()
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.autocommit = True

    # ── Verifikation ────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM scrape_groups")
    n_groups = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM scrape_runs")
    n_runs = cur.fetchone()[0]
    ok = (n_groups == len(groups)) and (n_runs == len(runs))
    print(f"\nPG: {n_groups:,} Gruppen, {n_runs:,} Runs — "
          f"{'✓ Zahlen stimmen' if ok else '✗ ABWEICHUNG!'}")

    sqlite_conn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    sq_stats = dict(sqlite_conn.execute(
        "SELECT status, COUNT(*) FROM scrape_groups GROUP BY status"))
    sqlite_conn.close()
    cur.execute("SELECT status, COUNT(*) FROM scrape_groups GROUP BY status")
    pg_stats = dict(cur.fetchall())
    print("\nStatus-Verteilung (SQLite → PG):")
    for status in sorted(set(sq_stats) | set(pg_stats)):
        a, b = sq_stats.get(status, 0), pg_stats.get(status, 0)
        marker = "✓" if a == b else "✗"
        print(f"  {marker} {status:<8} {a:>7,} → {b:>7,}")
        ok = ok and (a == b)

    pg.close()
    if not ok:
        print("\nFEHLER: Verifikation fehlgeschlagen.")
        return 1
    print("\nMigration erfolgreich. scraper.db kann archiviert werden "
          "(Backup-Skript sichert sie nicht mehr).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
