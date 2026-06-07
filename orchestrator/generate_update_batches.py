#!/usr/bin/env python3
"""Generate monthly update batches for the "rest" population (~95.000 Spieler)
that are not covered by the 4 priority update jobs (UP-ELO2300/FEMALE/GER/DACH).

Diese Spieler haben bereits eine vollständige Scrape-Historie, aber keinen
automatischen monatlichen Refresh-Mechanismus. Das Skript bündelt sie pro
Föderation (große Föderationen in ELO-Unterbänder gesplittet, Ziel 3.000-6.000
Spieler/Batch) in neue scrape_groups mit thread_affinity='dc_update' und
update_only=1 — der Worker wählt dann nur bereits gescrapte Spieler aus
(siehe get_fide_ids(..., update_only=True)), unabhängig von Rating-Drift.

Einmaliger Initial-Lauf. Neu hinzukommende Spieler landen automatisch im
passenden Föderations-Batch, sobald sie erstmals vollständig gescraped wurden
(scrape_periods-EXISTS-Filter) — kein Re-Balancing nötig.

Usage:
    python orchestrator/generate_update_batches.py [--dry-run] [--db PATH]
"""

import argparse
import math
import sys
from datetime import date
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.generate_groups import federation_continent
from orchestrator.setup_db import DB_PATH, create_db
from scraper.config import get_database_url

# Rest-Population: aktive, bereits gescrapte Spieler außerhalb der 4 Prioritätsfilter
# (ELO >= 2300, weiblich, GER/SUI/AUT) — siehe update_jobs.yaml
REST_POPULATION_SQL = """
    SELECT p.federation, p.std_rating
    FROM players p
    WHERE p.active = TRUE
      AND (p.sex IS DISTINCT FROM 'F')
      AND p.federation NOT IN ('GER', 'SUI', 'AUT')
      AND p.std_rating < 2300
      AND EXISTS (
          SELECT 1 FROM scrape_periods sp
          WHERE sp.fide_id = p.fide_id AND sp.status = 'ok'
      )
    ORDER BY p.federation, p.std_rating DESC
"""

REST_ELO_FLOOR = 0      # Rest-Population ist nicht auf ELO_FLOOR=1400 beschränkt
REST_ELO_CEIL = 2299    # Obergrenze: ab 2300 übernimmt UP-ELO2300

TARGET_MIN = 3000
TARGET_MAX = 6000


def load_rest_population() -> dict[str, list[int]]:
    """Return {federation: [ratings desc]} for the rest population."""
    conn = psycopg2.connect(get_database_url())
    by_fed: dict[str, list[int]] = {}
    with conn.cursor() as cur:
        cur.execute(REST_POPULATION_SQL)
        for federation, std_rating in cur.fetchall():
            by_fed.setdefault(federation, []).append(std_rating)
    conn.close()
    return by_fed


def _split_chunks(ratings_desc: list[int]) -> list[list[int]]:
    """Split a sorted-descending rating list into TARGET_MIN..TARGET_MAX chunks.

    Federations within the target range stay a single chunk. Larger federations
    (currently only ESP, IND) are split into the fewest possible roughly-equal
    chunks that stay within TARGET_MAX (and, where possible, at or above TARGET_MIN).
    """
    n = len(ratings_desc)
    if n <= TARGET_MAX:
        return [ratings_desc]

    k = math.ceil(n / TARGET_MAX)
    while k > 1 and n / k < TARGET_MIN:
        k -= 1

    base, remainder = divmod(n, k)
    chunks = []
    idx = 0
    for i in range(k):
        size = base + (1 if i < remainder else 0)
        chunks.append(ratings_desc[idx:idx + size])
        idx += size
    return chunks


def build_federation_bands(ratings_desc: list[int], federation: str, year: int, sqlite_conn) -> list[dict]:
    """Build contiguous, non-overlapping ELO bands for one federation.

    elo_min must be unique per (federation, year) — UNIQUE constraint on
    scrape_groups. Boundaries are threaded top-down: each band's elo_max is
    derived from the *adjusted* elo_min of the band above it, so a uniqueness
    nudge never creates a gap or overlap with its neighbour.
    """
    chunks = _split_chunks(ratings_desc)

    existing = {
        row[0] for row in sqlite_conn.execute(
            "SELECT elo_min FROM scrape_groups WHERE federation = ? AND year = ?",
            (federation, year),
        ).fetchall()
    }
    taken: set[int] = set()

    def unique_elo_min(candidate: int) -> int:
        while candidate in existing or candidate in taken:
            candidate -= 1
        taken.add(candidate)
        return candidate

    bands = []
    elo_max = REST_ELO_CEIL
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        raw_elo_min = REST_ELO_FLOOR if is_last else chunk[-1]
        elo_min = unique_elo_min(raw_elo_min)
        bands.append({"elo_min": elo_min, "elo_max": elo_max, "player_count": len(chunk)})
        elo_max = elo_min - 1

    return bands


def build_groups(by_fed: dict[str, list[int]], year: int, sqlite_conn) -> list[dict]:
    groups = []

    for federation, ratings in by_fed.items():
        bands = build_federation_bands(ratings, federation, year, sqlite_conn)
        for band in bands:
            groups.append({
                "federation": federation,
                "continent": federation_continent(federation),
                "year": year,
                "elo_min": band["elo_min"],
                "elo_max": band["elo_max"],
                "player_count": band["player_count"],
                "status": "pending",
                "thread_affinity": "dc_update",
                "update_only": 1,
            })

    # Größere Batches zuerst (laufen länger, sollen den Monatszyklus früh starten)
    groups.sort(key=lambda g: -g["player_count"])
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
    """Sanity check: per-federation bands must be contiguous and non-overlapping."""
    by_fed: dict[str, list[dict]] = {}
    for g in groups:
        by_fed.setdefault(g["federation"], []).append(g)
    for federation, fed_groups in by_fed.items():
        ordered = sorted(fed_groups, key=lambda g: -g["elo_min"])
        for upper, lower in zip(ordered, ordered[1:]):
            assert lower["elo_max"] == upper["elo_min"] - 1, (
                f"{federation}: Lücke/Überlappung zwischen "
                f"ELO {lower['elo_min']}-{lower['elo_max']} und {upper['elo_min']}-{upper['elo_max']}"
            )


def print_preview(groups: list[dict]) -> None:
    _check_contiguous(groups)
    print(f"\nTotal update batches: {len(groups):,}")
    total_players = sum(g["player_count"] for g in groups)
    print(f"Total players covered: {total_players:,}")

    counts = [g["player_count"] for g in groups]
    print(f"Players per batch: min={min(counts):,}  max={max(counts):,}  "
          f"avg={sum(counts) / len(counts):,.0f}")
    out_of_range = [g for g in groups if not (TARGET_MIN <= g["player_count"] <= TARGET_MAX)]
    print(f"Batches outside {TARGET_MIN:,}-{TARGET_MAX:,}: {len(out_of_range)}")

    print("\nBatches (sorted by size, descending):")
    for g in groups:
        print(f"  {g['federation']:4s}  ELO {g['elo_min']:5d}–{g['elo_max']:4d}  "
              f"{g['player_count']:>6,} Spieler  prio={g['priority']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate dc_update batches for the rest population")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht schreiben")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Pfad zur SQLite-DB")
    parser.add_argument("--year", type=int, default=date.today().year)
    args = parser.parse_args()

    print("Lade Rest-Population aus PostgreSQL ...")
    by_fed = load_rest_population()
    total = sum(len(r) for r in by_fed.values())
    print(f"{len(by_fed)} Föderationen, {total:,} Spieler (rest population)")

    sqlite_conn = create_db(args.db)
    groups = build_groups(by_fed, args.year, sqlite_conn)
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
