#!/usr/bin/env python3
"""Generate monthly-refresh batches for the P1/P2/P3 priority tiers.

Ersetzt das frühere generate_update_batches.py (föderationsbasierte Batches
nur für die "Rest"-Population; 2026-07 gelöscht, siehe Git-Historie). Statt
dessen werden hier drei geschlechtsunabhängige, nicht-überlappende
Prioritätsstufen abgedeckt (siehe orchestrator/monthly_refresh_tiers.py):

    P1  ELO >= 2300, alle Föderationen
    P2  DACH (GER/SUI/AUT), ELO < 2300
    P3  Rest — alle übrigen bereits gescrapten, aktiven Spieler

Batches werden NICHT mehr pro Föderation gebildet, sondern rein nach ELO-Band
über die gepoolte Tier-Population — die Spieleranzahl wächst monatlich, neue
Spieler landen automatisch im passenden Band (siehe get_fide_ids() in
worker.py, EXISTS(scrape_periods)-Filter zur Scrape-Zeit), ohne dass dieses
Skript erneut laufen müsste.

Alle Batches erhalten thread_affinity aus dem DC_UPDATE_POOL (siehe
monthly_refresh_tiers.py) per Greedy-Load-Balancing (LPT: größte Batches
zuerst, jeweils dem Pool-Thread mit aktuell kleinster Summe zuweisen), und
update_only=1 (nur bereits gescrapte Spieler, kein Vollbackfill-Risiko).

Usage:
    python orchestrator/generate_monthly_refresh_batches.py [--dry-run] [--db PATH] [--year YYYY]
"""

import argparse
import math
import sys
from datetime import date
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.monthly_refresh_tiers import (
    DC_UPDATE_POOL,
    TIER_BOUNDS,
    TIER_CONTINENT,
    TIER_FILTERS,
    TIER_TARGET_MAX,
    TIER_TARGET_MIN,
    TIERS,
)
from orchestrator.setup_db import DB_PATH, create_db
from scraper.config import get_database_url


def load_tier_population(tier: str) -> list[int]:
    """Return [ratings desc] for one tier, pooled across all federations."""
    conn = psycopg2.connect(get_database_url())
    ratings: list[int] = []
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT p.std_rating
            FROM players p
            WHERE p.active = TRUE
              AND {TIER_FILTERS[tier]}
              AND EXISTS (
                  SELECT 1 FROM scrape_periods sp
                  WHERE sp.fide_id = p.fide_id AND sp.status = 'ok'
              )
            ORDER BY p.std_rating DESC
            """
        )
        ratings = [row[0] for row in cur.fetchall()]
    conn.close()
    return ratings


def _split_chunks(ratings_desc: list[int]) -> list[list[int]]:
    """Split a sorted-descending rating list into TIER_TARGET_MIN..MAX chunks.

    Populations within the target range stay a single chunk. Larger
    populations are split into the fewest possible roughly-equal chunks that
    stay within TIER_TARGET_MAX (and, where possible, at or above
    TIER_TARGET_MIN).
    """
    n = len(ratings_desc)
    if n <= TIER_TARGET_MAX:
        return [ratings_desc]

    k = math.ceil(n / TIER_TARGET_MAX)
    while k > 1 and n / k < TIER_TARGET_MIN:
        k -= 1

    base, remainder = divmod(n, k)
    chunks = []
    idx = 0
    for i in range(k):
        size = base + (1 if i < remainder else 0)
        chunks.append(ratings_desc[idx:idx + size])
        idx += size
    return chunks


def build_tier_bands(ratings_desc: list[int], tier: str, year: int, sqlite_conn) -> list[dict]:
    """Build contiguous, non-overlapping ELO bands for one tier.

    elo_min must be unique per (federation, year) — the tier sentinel
    ('P1'/'P2'/'P3') is stored in the federation column, so this UNIQUE
    constraint scopes cleanly per tier. Boundaries are threaded top-down from
    the tier's elo_ceil; on collision with an existing elo_min the candidate
    is nudged down by 1 until free.
    """
    elo_floor, elo_ceil = TIER_BOUNDS[tier]
    chunks = _split_chunks(ratings_desc)

    existing = {
        row[0] for row in sqlite_conn.execute(
            "SELECT elo_min FROM scrape_groups WHERE federation = ? AND year = ?",
            (tier, year),
        ).fetchall()
    }
    taken: set[int] = set()

    def unique_elo_min(candidate: int) -> int:
        while candidate in existing or candidate in taken:
            candidate -= 1
        taken.add(candidate)
        return candidate

    bands = []
    elo_max = elo_ceil
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        raw_elo_min = elo_floor if is_last else chunk[-1]
        elo_min = unique_elo_min(raw_elo_min)
        bands.append({"elo_min": elo_min, "elo_max": elo_max, "player_count": len(chunk)})
        elo_max = elo_min - 1

    return bands


def _assign_thread_affinity(groups: list[dict]) -> None:
    """Greedy load-balancing (LPT): largest batches first, each assigned to
    whichever pool thread currently carries the smallest cumulative
    player_count. Mutates groups in place."""
    load = {t: 0 for t in DC_UPDATE_POOL}
    for g in sorted(groups, key=lambda g: -g["player_count"]):
        thread = min(load, key=load.get)
        g["thread_affinity"] = thread
        load[thread] += g["player_count"]


def build_groups(year: int, sqlite_conn) -> list[dict]:
    groups = []

    for tier in TIERS:
        ratings = load_tier_population(tier)
        bands = build_tier_bands(ratings, tier, year, sqlite_conn)
        for band in bands:
            groups.append({
                "federation": tier,
                "continent": TIER_CONTINENT,
                "year": year,
                "elo_min": band["elo_min"],
                "elo_max": band["elo_max"],
                "player_count": band["player_count"],
                "status": "pending",
                "update_only": 1,
            })

    _assign_thread_affinity(groups)

    # Tier-Reihenfolge geht vor Batch-Größe: P1 muss vollständig vor P2 laufen,
    # P2 vollständig vor P3 — besonders wichtig bei nur einem dc_update-Thread,
    # wo die Gruppen strikt sequenziell nach priority abgearbeitet werden.
    # Innerhalb eines Tiers: größere Batches zuerst (laufen länger, sollen früh starten).
    tier_rank = {tier: i for i, tier in enumerate(TIERS)}
    groups.sort(key=lambda g: (tier_rank[g["federation"]], -g["player_count"]))
    base = sqlite_conn.execute("SELECT COALESCE(MAX(priority), 0) FROM scrape_groups").fetchone()[0]
    for rank, g in enumerate(groups, start=1):
        g["priority"] = base + rank

    return groups


def insert_groups(groups: list[dict], db_path: Path) -> tuple[int, int]:
    conn = create_db(db_path)
    inserted = skipped = 0
    for g in groups:
        try:
            conn.execute(
                """
                INSERT INTO scrape_groups
                    (federation, continent, year, elo_min, elo_max, player_count,
                     status, priority, thread_affinity, update_only)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (g["federation"], g["continent"], g["year"], g["elo_min"], g["elo_max"],
                 g["player_count"], g["status"], g["priority"],
                 g["thread_affinity"], g["update_only"]),
            )
            inserted += 1
        except Exception:
            skipped += 1
    conn.commit()
    conn.close()
    return inserted, skipped


def _check_contiguous(groups: list[dict]) -> None:
    """Sanity check: per-tier bands must be contiguous and non-overlapping."""
    by_tier: dict[str, list[dict]] = {}
    for g in groups:
        by_tier.setdefault(g["federation"], []).append(g)
    for tier, tier_groups in by_tier.items():
        ordered = sorted(tier_groups, key=lambda g: -g["elo_min"])
        for upper, lower in zip(ordered, ordered[1:]):
            assert lower["elo_max"] == upper["elo_min"] - 1, (
                f"{tier}: Lücke/Überlappung zwischen "
                f"ELO {lower['elo_min']}-{lower['elo_max']} und {upper['elo_min']}-{upper['elo_max']}"
            )


def print_preview(groups: list[dict]) -> None:
    _check_contiguous(groups)
    print(f"\nTotal monthly-refresh batches: {len(groups):,}")
    total_players = sum(g["player_count"] for g in groups)
    print(f"Total players covered: {total_players:,}")

    for tier in TIERS:
        tier_groups = [g for g in groups if g["federation"] == tier]
        tier_total = sum(g["player_count"] for g in tier_groups)
        print(f"  {tier}: {len(tier_groups)} Batches, {tier_total:,} Spieler")

    counts = [g["player_count"] for g in groups]
    print(f"\nPlayers per batch: min={min(counts):,}  max={max(counts):,}  "
          f"avg={sum(counts) / len(counts):,.0f}")
    out_of_range = [g for g in groups if not (TIER_TARGET_MIN <= g["player_count"] <= TIER_TARGET_MAX)]
    print(f"Batches outside {TIER_TARGET_MIN:,}-{TIER_TARGET_MAX:,}: {len(out_of_range)}")

    load: dict[str, int] = {}
    for g in groups:
        load[g["thread_affinity"]] = load.get(g["thread_affinity"], 0) + g["player_count"]
    print("\nThread-Verteilung (Ziel: annähernd gleich):")
    for thread, total in sorted(load.items()):
        print(f"  {thread}: {total:,} Spieler")

    print("\nBatches (Abarbeitungsreihenfolge: Tier P1→P2→P3, je Tier größte zuerst):")
    for g in groups:
        print(f"  {g['federation']:2s}  ELO {g['elo_min']:5d}–{g['elo_max']:4d}  "
              f"{g['player_count']:>6,} Spieler  thread={g['thread_affinity']}  prio={g['priority']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate P1/P2/P3 monthly-refresh batches")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht schreiben")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Pfad zur SQLite-DB")
    parser.add_argument("--year", type=int, default=date.today().year)
    args = parser.parse_args()

    sqlite_conn = create_db(args.db)

    print("Lade P1/P2/P3-Populationen aus PostgreSQL ...")
    groups = build_groups(args.year, sqlite_conn)
    print_preview(groups)

    if args.dry_run:
        sqlite_conn.close()
        print("\n[--dry-run: nichts geschrieben]")
        return 0

    sqlite_conn.close()
    print(f"\nSchreibe nach {args.db} ...")
    inserted, skipped = insert_groups(groups, args.db)
    print(f"Fertig: {inserted:,} eingefügt, {skipped:,} übersprungen (existieren bereits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
