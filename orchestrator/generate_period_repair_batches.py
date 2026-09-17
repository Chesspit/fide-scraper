#!/usr/bin/env python3
"""Generate GAP batches — eine Periode für Spieler nachscrapen, die dort fehlen.

Population: aktive Spieler mit std_rating > 0, die laut offizieller Liste in
--period Partien hatten (rating_history.num_games > 0), aber keinen
scrape_periods-Eintrag für diese Periode haben. Genau diese Menge wählt auch
worker.py::get_fide_ids(only_period=...) zur Laufzeit aus.

Anlass (2026-09-17): Die Liste 2024-06 war mit einer Juli-Fassung vertauscht,
der Pre-Filter hat deshalb 22.830 Juni-Kombos ohne Abruf als no_data markiert.
Nach dem Neuimport der Liste und dem Löschen dieser Zeilen holen die GAP-
Batches die Periode nach, ohne das ganze Jahr neu aufzurollen.

Batches laufen standardmäßig über NEW_ENTRANT_POOL (dc_newplayers_1/2).

Usage:
    python orchestrator/generate_period_repair_batches.py --period 2024-06-01 [--dry-run]
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.generate_monthly_refresh_batches import (
    _assign_thread_affinity,
    _check_contiguous,
    build_tier_bands,
    insert_groups,
)
from orchestrator.monthly_refresh_tiers import GAP_TIER, NEW_ENTRANT_POOL, TIER_CONTINENT
from orchestrator.setup_db import connect
from scraper.config import get_database_url

# Eine Kombo je Spieler; bei ~260-330 Kombos/h/Thread ≈ 6-8 Std./Batch —
# passt in ein Tagesfenster (active_hours werden nur beim Claimen geprüft).
TARGET_MIN = 1500
TARGET_MAX = 2200


def load_population(period: str) -> list[int]:
    """Return [ratings desc] of players to repair for this period."""
    conn = psycopg2.connect(get_database_url())
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.std_rating
            FROM rating_history rh
            JOIN players p ON p.fide_id = rh.fide_id
            WHERE rh.period = %s AND rh.num_games > 0
              AND p.active = TRUE
              AND p.std_rating > 0
              AND NOT EXISTS (SELECT 1 FROM scrape_periods sp
                              WHERE sp.fide_id = rh.fide_id AND sp.period = rh.period)
            ORDER BY p.std_rating DESC
            """,
            (period,),
        )
        ratings = [row[0] for row in cur.fetchall()]
    conn.close()
    return ratings


def build_groups(period: date, pool: list[str], queue_conn) -> list[dict]:
    ratings = load_population(period.isoformat())
    if not ratings:
        return []
    bands = build_tier_bands(ratings, GAP_TIER, period.year, queue_conn,
                             target_min=TARGET_MIN, target_max=TARGET_MAX)
    groups = [{
        "federation": GAP_TIER,
        "continent": TIER_CONTINENT,
        "year": period.year,
        "elo_min": b["elo_min"],
        "elo_max": b["elo_max"],
        "player_count": b["player_count"],
        "status": "pending",
        "update_only": 0,
        "only_period": period,
    } for b in bands]
    _assign_thread_affinity(groups, pool=pool)

    groups.sort(key=lambda g: -g["player_count"])
    with queue_conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(priority), 0) FROM scrape_groups")
        base = cur.fetchone()[0]
    for rank, g in enumerate(groups, start=1):
        g["priority"] = base + rank
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GAP (period repair) batches")
    parser.add_argument("--period", required=True, help="Periode YYYY-MM-01")
    parser.add_argument("--threads", default=",".join(NEW_ENTRANT_POOL),
                        help="Thread-Pool, kommagetrennt (Default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht schreiben")
    args = parser.parse_args()

    period = date.fromisoformat(args.period)
    if period.day != 1:
        parser.error("--period muss ein Monatserster sein")
    pool = [t.strip() for t in args.threads.split(",") if t.strip()]

    queue_conn = connect()
    groups = build_groups(period, pool, queue_conn)
    if not groups:
        print(f"Keine fehlenden Kombos für {period} — nichts zu tun.")
        queue_conn.close()
        return 0

    _check_contiguous(groups)
    print(f"GAP {period}: {len(groups)} Batches, "
          f"{sum(g['player_count'] for g in groups):,} Spieler")
    for g in groups:
        print(f"  ELO {g['elo_min']:5d}–{g['elo_max']:4d}  {g['player_count']:>6,} Spieler  "
              f"thread={g['thread_affinity']}  prio={g['priority']}")

    if args.dry_run:
        queue_conn.close()
        print("\n[--dry-run: nichts geschrieben]")
        return 0

    inserted, skipped = insert_groups(groups, queue_conn)
    queue_conn.close()
    print(f"\n{inserted} eingefügt, {skipped} übersprungen (Konflikt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
