#!/usr/bin/env python3
"""Generate P0 ("new entrants") batches — players who were NEVER scraped.

Ergänzt generate_monthly_refresh_batches.py (P1/P2/P3) um genau die Population,
die dort strukturell ausgeschlossen bleibt: Spieler ohne einen einzigen
scrape_periods-Eintrag. P1/P2/P3 verlangen alle EXISTS(status='ok') — ein nie
gescrapter Spieler bleibt dort für immer unsichtbar, auch wenn seine
Föderations-Gruppe längst 'done' ist (siehe Diskussion 2026-09-01,
Memory std-rating-sync-fix-2026-09-01).

P0-Gruppen erhalten update_only=2 (Sentinel für "nur nie versuchte Spieler",
siehe worker.py::get_fide_ids(never_scraped_only=...)) und laufen über einen
eigenen Thread-Pool (NEW_ENTRANT_POOL = dc_newplayers_1/2), nicht über
DC_UPDATE_POOL — parallel zum laufenden P1/P2/P3-Refresh, ohne dessen Threads
zu verdrängen.

Backfill-Tiefe: pro --year ein Satz Bänder, jeweils die vollen gültigen
Perioden dieses Jahres (siehe worker.py::valid_periods_for_year()). Für den
initialen Nachscrape (Phase 1.4) reicht i.d.R. --year 2026 (deckt Jan-Aug
2026, ~8 Monate zurück); --year 2025 zusätzlich für tiefere Historie.

Usage:
    python orchestrator/generate_new_entrant_batches.py --year 2026 [--dry-run]
    python orchestrator/generate_new_entrant_batches.py --year 2026 --year 2025
"""

import argparse
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.generate_monthly_refresh_batches import (
    _assign_thread_affinity,
    _check_contiguous,
    _split_chunks,
    build_tier_bands,
    insert_groups,
)
from orchestrator.monthly_refresh_tiers import (
    NEW_ENTRANT_POOL,
    NEW_ENTRANT_TIERS,
    TIER_CONTINENT,
)
from orchestrator.setup_db import connect
from scraper.config import get_database_url

UPDATE_ONLY_NEVER_SCRAPED = 2


def load_new_entrant_population() -> list[int]:
    """Return [ratings desc] for P0: active, current std list, NEVER scraped.

    Umgekehrter Filter ggü. load_tier_population() in
    generate_monthly_refresh_batches.py (NOT EXISTS statt EXISTS status='ok') —
    absichtlich eine eigene, kleine Funktion statt die bestehende zu
    überladen, um das laufende P1/P2/P3-System nicht anzufassen.
    """
    conn = psycopg2.connect(get_database_url())
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT MAX(period) AS p FROM rating_history WHERE published_rating IS NOT NULL
            )
            SELECT p.std_rating
            FROM rating_history rh
            JOIN players p ON p.fide_id = rh.fide_id
            CROSS JOIN latest
            WHERE rh.period = latest.p AND rh.published_rating IS NOT NULL
              AND p.active = TRUE
              AND NOT EXISTS (SELECT 1 FROM scrape_periods sp WHERE sp.fide_id = p.fide_id)
            ORDER BY p.std_rating DESC
            """
        )
        ratings = [row[0] for row in cur.fetchall()]
    conn.close()
    return ratings


def build_groups(years: list[int], queue_conn) -> list[dict]:
    groups = []

    ratings = load_new_entrant_population()
    for year in years:
        bands = build_tier_bands(ratings, "P0", year, queue_conn)
        for band in bands:
            groups.append({
                "federation": "P0",
                "continent": TIER_CONTINENT,
                "year": year,
                "elo_min": band["elo_min"],
                "elo_max": band["elo_max"],
                "player_count": band["player_count"],
                "status": "pending",
                "update_only": UPDATE_ONLY_NEVER_SCRAPED,
            })

    _assign_thread_affinity(groups, pool=NEW_ENTRANT_POOL)

    # Innerhalb P0 keine Tier-Reihenfolge nötig (nur ein Tier) — größere
    # Bänder zuerst, laufen länger, sollen früh starten.
    groups.sort(key=lambda g: -g["player_count"])
    with queue_conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(priority), 0) FROM scrape_groups")
        base = cur.fetchone()[0]
    for rank, g in enumerate(groups, start=1):
        g["priority"] = base + rank

    return groups


def print_preview(groups: list[dict]) -> None:
    # _check_contiguous gruppiert nur nach federation (P1/P2/P3 rufen den
    # Generator immer mit genau einem Jahr auf) — P0 kann mehrere Jahre in
    # einem Lauf bauen, deshalb hier pro Jahr separat prüfen.
    for year in sorted({g["year"] for g in groups}):
        _check_contiguous([g for g in groups if g["year"] == year])
    print(f"\nTotal new-entrant batches: {len(groups):,}")
    total_players = sum(g["player_count"] for g in groups)
    print(f"Total players covered: {total_players:,} (pro Jahr identische Population, "
          f"unterschiedliche Zeiträume)")

    years = sorted({g["year"] for g in groups})
    for year in years:
        yg = [g for g in groups if g["year"] == year]
        yt = sum(g["player_count"] for g in yg)
        print(f"  Jahr {year}: {len(yg)} Batches, {yt:,} Spieler")

    load: dict[str, int] = {}
    for g in groups:
        load[g["thread_affinity"]] = load.get(g["thread_affinity"], 0) + g["player_count"]
    print("\nThread-Verteilung (Ziel: annähernd gleich):")
    for thread, total in sorted(load.items()):
        print(f"  {thread}: {total:,} Spieler")

    print("\nBatches (Abarbeitungsreihenfolge: größte zuerst):")
    for g in groups:
        print(f"  P0  Jahr {g['year']}  ELO {g['elo_min']:5d}–{g['elo_max']:4d}  "
              f"{g['player_count']:>6,} Spieler  thread={g['thread_affinity']}  prio={g['priority']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate P0 (new-entrants) batches")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht schreiben")
    parser.add_argument("--year", type=int, action="append", required=True,
                        help="Jahr(e), für die Bänder gebaut werden (mehrfach angebbar)")
    args = parser.parse_args()

    queue_conn = connect()

    print(f"Lade P0-Population (aktiv, aktuelle Std-Liste, nie gescraped) aus PostgreSQL ...")
    groups = build_groups(sorted(set(args.year)), queue_conn)
    print_preview(groups)

    if args.dry_run:
        queue_conn.close()
        print("\n[--dry-run: nichts geschrieben]")
        return 0

    print("\nSchreibe nach orchestrator.scrape_groups ...")
    inserted, skipped = insert_groups(groups, queue_conn)
    queue_conn.close()
    print(f"Fertig: {inserted:,} eingefügt, {skipped:,} übersprungen (existieren bereits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
