#!/usr/bin/env python3
"""Generate scrape groups (federation × year × ELO-band) and populate the queue.

ELO bands are computed dynamically per federation, targeting 50–250 players/band.
Years 2009–2026 with FIDE-valid periods per year (from is_valid_fide_period logic).
Ziel ist seit Review #5 das Schema "orchestrator" in PostgreSQL (DATABASE_URL).

Usage:
    python orchestrator/generate_groups.py [--preview]
"""

import argparse
import hashlib
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.setup_db import connect
from scripts.seed_players import load_players_from_file

# ---------------------------------------------------------------------------
# FIDE continent mapping  (federation code → continent)
# FID = stateless/international → Europe per design decision
# ---------------------------------------------------------------------------
_EUROPE = {
    "ALB", "AND", "ARM", "AUT", "AZE", "BEL", "BIH", "BLR", "BUL", "CRO",
    "CYP", "CZE", "DEN", "ENG", "ESP", "EST", "FAI", "FID", "FIN", "FRA",
    "GCI", "GEO", "GER", "GIB", "GRE", "HUN", "ICE", "IOM", "IRL", "ISL",
    "ISR", "ITA", "JCI", "KOS", "LAT", "LIE", "LIT", "LTU", "LUX", "MDA",
    "MKD", "MLT", "MNC", "MNE", "NED", "NOR", "POL", "POR", "ROU", "RUS",
    "SCO", "SLO", "SMR", "SRB", "SUI", "SVK", "SWE", "TUR", "UKR", "WAL",
    "WLS",
}
_ASIA = {
    "AFG", "BAN", "BHU", "BRN", "BRU", "CAM", "CHN", "HKG", "INA", "IND",
    "IRI", "IRQ", "JOR", "JPN", "KAZ", "KGZ", "KOR", "KSA", "KUW", "LAO",
    "LBN", "LKA", "MAC", "MAS", "MDV", "MGL", "MYA", "NEP", "OMA", "PAK",
    "PHI", "PLE", "PRK", "QAT", "SAU", "SGP", "SIN", "SRI", "SYR", "THA",
    "TJK", "TKM", "TLS", "TPE", "UAE", "UZB", "VIE", "YEM",
}
_AMERICAS = {
    "AHO", "ANT", "ARG", "ARU", "BAH", "BAR", "BER", "BIZ", "BOL", "BRA",
    "CAN", "CAY", "CHI", "COL", "CRC", "CUB", "DMA", "DOM", "ECU", "ESA",
    "GRL", "GRN", "GUA", "GUY", "HAI", "HON", "ISV", "IVB", "JAM", "LCA",
    "MEX", "MSN", "NCA", "PAN", "PAR", "PER", "PUR", "SKN", "SUR", "TTO",
    "URU", "USA", "VEN", "VGB", "VIN",
}
_AFRICA = {
    "ALG", "ANG", "BDI", "BEN", "BOT", "BUR", "CAF", "CGO", "CHA", "CIV",
    "CMR", "COD", "COM", "CPV", "DJI", "EGY", "ERI", "ETH", "GAB", "GAM",
    "GEQ", "GHA", "GNB", "GUI", "KEN", "LBA", "LBR", "LES", "LIB", "MAD",
    "MAR", "MAU", "MAW", "MLI", "MOZ", "MRI", "MTN", "NAM", "NGR", "NIG",
    "NGA", "RSA", "RWA", "SEN", "SEY", "SLE", "SOM", "SSD", "STP", "SUD",
    "SWZ", "TAN", "TOG", "TUN", "UGA", "ZAM", "ZIM",
}
_OCEANIA = {
    "AUS", "COK", "FIJ", "GUM", "MHL", "NCL", "NRU", "NZL", "PLW", "PNG",
    "SAM", "SOL", "TGA", "TON", "TUV", "VAN",
}


def federation_continent(fed: str) -> str:
    fed = (fed or "").strip().upper()
    if fed in _EUROPE:
        return "Europe"
    if fed in _ASIA:
        return "Asia"
    if fed in _AMERICAS:
        return "Americas"
    if fed in _AFRICA:
        return "Africa"
    if fed in _OCEANIA:
        return "Oceania"
    return "Other"


# ---------------------------------------------------------------------------
# Valid FIDE periods per year  (mirrors db.py::is_valid_fide_period)
# ---------------------------------------------------------------------------
def periods_per_year(year: int) -> int:
    """Number of valid FIDE periods in the given calendar year."""
    if year < 2008:
        return 0
    if year == 2008:
        return 3   # Apr, Jul, Oct
    if year == 2009:
        return 5   # Jan, Apr, Jul, Sep, Nov
    if year <= 2011:
        return 6   # bi-monthly: Jan,Mar,May,Jul,Sep,Nov
    if year == 2012:
        return 11  # Jan,Mar,May,Jul (bi-monthly) + Aug–Dec (monthly) = 4+5 = 9… actually:
                   # bi-monthly Jan/Mar/May/Jul = 4, monthly Aug-Dec = 5 → 9
    # 2012 correction: Jan Mar May Jul = 4 bi-monthly, Aug Sep Oct Nov Dec = 5 monthly
    return 12      # 2013+: fully monthly


def _periods_2012() -> int:
    return 9  # Jan Mar May Jul Aug Sep Oct Nov Dec


def periods_in_year(year: int) -> int:
    if year == 2012:
        return _periods_2012()
    return periods_per_year(year)


# ---------------------------------------------------------------------------
# ELO band computation
# ---------------------------------------------------------------------------
ELO_FLOOR = 1400
MIN_SIZE   = 50
MAX_SIZE   = 250

# Gewichtete Größenverteilung: 10% klein / 20% mittel / 40% groß / 30% sehr groß
_SIZE_BUCKETS  = [(50, 99), (100, 149), (150, 199), (200, 250)]
_SIZE_WEIGHTS  = [10, 20, 40, 30]


def _fed_rng(federation: str) -> random.Random:
    """Reproducible RNG seeded from the federation code."""
    seed = int(hashlib.md5(federation.encode()).hexdigest(), 16) % (2 ** 32)
    return random.Random(seed)


def _random_band_size(rng: random.Random, max_allowed: int) -> int:
    """Pick a weighted-random band size, capped at max_allowed."""
    bucket = rng.choices(range(len(_SIZE_BUCKETS)), weights=_SIZE_WEIGHTS, k=1)[0]
    lo, hi = _SIZE_BUCKETS[bucket]
    desired = rng.randint(lo, hi)
    return max(MIN_SIZE, min(desired, max_allowed))


def compute_bands(ratings_desc: list[int], federation: str = "") -> list[dict]:
    """Split a sorted-descending list of ratings into ELO bands with weighted sizes.

    Distribution: 10% → 50-99 players, 20% → 100-149, 40% → 150-199, 30% → 200-250.
    Reproducible per federation — same input always produces the same bands.
    """
    n = len(ratings_desc)
    if n == 0:
        return []
    if n < MIN_SIZE:
        return [{"elo_min": ELO_FLOOR, "elo_max": ratings_desc[0], "player_count": n}]

    rng = _fed_rng(federation)
    raw_bands: list[list[int]] = []
    remaining = list(ratings_desc)

    while len(remaining) >= MIN_SIZE:
        leftover = len(remaining)
        if leftover <= MAX_SIZE:
            raw_bands.append(remaining)
            remaining = []
            break
        # Ensure we don't leave < MIN_SIZE behind
        max_take = leftover - MIN_SIZE
        size = _random_band_size(rng, max_take)
        raw_bands.append(remaining[:size])
        remaining = remaining[size:]

    # Absorb tiny leftovers into the last band
    if remaining:
        if raw_bands:
            raw_bands[-1] = raw_bands[-1] + remaining
        else:
            raw_bands.append(remaining)

    result = []
    upper_bound = raw_bands[0][0]
    for i, chunk in enumerate(raw_bands):
        result.append({
            "elo_min": ELO_FLOOR if i == len(raw_bands) - 1 else chunk[-1],
            "elo_max": upper_bound,
            "player_count": len(chunk),
        })
        upper_bound = chunk[-1] - 1

    return result


# ---------------------------------------------------------------------------
# Sort key for priority ranking (not stored — used only to rank groups)
# newest year + highest ELO → lowest rank → priority 1
# ---------------------------------------------------------------------------
BASE_YEAR = 2026


def _sort_key(g: dict) -> tuple:
    """Lower = higher priority: newest year first, highest ELO-band first."""
    return (-(g["year"]), -(g["elo_min"]))


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------
def load_active_players(txt_path: Path) -> dict[str, list[int]]:
    """Return {federation: [ratings desc]} for active players with rating >= ELO_FLOOR."""
    players = load_players_from_file(txt_path)
    by_fed: dict[str, list[int]] = defaultdict(list)
    for p in players:
        if p.get("active") and p.get("std_rating", 0) >= ELO_FLOOR and p.get("federation"):
            by_fed[p["federation"]].append(p["std_rating"])
    for fed in by_fed:
        by_fed[fed].sort(reverse=True)
    return dict(by_fed)


def generate_groups(txt_path: Path, years: range) -> list[dict]:
    """Return all group dicts ready for SQLite insertion, with sequential priorities.

    Priority 1 = highest importance (newest year + highest ELO), ascending integers.
    Groups with the same (year, elo_min) across federations share a fuzzy tier
    via TIER_WIDTH in queue_manager.py.
    """
    by_fed = load_active_players(txt_path)
    groups = []
    for fed, ratings in by_fed.items():
        continent = federation_continent(fed)
        bands = compute_bands(ratings, federation=fed)
        for band in bands:
            for year in years:
                if periods_in_year(year) == 0:
                    continue
                groups.append({
                    "federation": fed,
                    "continent": continent,
                    "year": year,
                    "elo_min": band["elo_min"],
                    "elo_max": band["elo_max"],
                    "player_count": band["player_count"],
                    "status": "pending",
                })

    # Assign sequential priorities: 1 = most important
    groups.sort(key=_sort_key)
    for rank, g in enumerate(groups, start=1):
        g["priority"] = rank

    return groups


def insert_groups(groups: list[dict]) -> tuple[int, int]:
    """Insert groups into orchestrator.scrape_groups. Returns (inserted, skipped)."""
    conn = connect()
    inserted = skipped = 0
    with conn.cursor() as cur:
        for g in groups:
            cur.execute(
                """
                INSERT INTO scrape_groups
                    (federation, continent, year, elo_min, elo_max,
                     player_count, status, priority)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (federation, year, elo_min) DO NOTHING
                """,
                (g["federation"], g["continent"], g["year"],
                 g["elo_min"], g["elo_max"], g["player_count"],
                 g["status"], g["priority"]),
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
    conn.close()
    return inserted, skipped


def print_preview(groups: list[dict]) -> None:
    from collections import Counter

    print(f"\nTotal groups: {len(groups):,}")

    # By continent
    by_cont: dict[str, int] = Counter(g["continent"] for g in groups)
    print("\nGroups per continent:")
    for cont, count in sorted(by_cont.items()):
        print(f"  {cont:<12} {count:>7,}")

    # Player count distribution
    counts = [g["player_count"] for g in groups]
    print(f"\nPlayers per band:")
    print(f"  min={min(counts)}  max={max(counts)}  "
          f"avg={sum(counts)/len(counts):.0f}")

    # Top 10 federations by group count
    fed_counts: dict[str, int] = Counter(g["federation"] for g in groups)
    print("\nTop 10 federations by group count:")
    for fed, cnt in fed_counts.most_common(10):
        bands = cnt // 18  # approximate bands per fed
        print(f"  {fed}  {cnt:>5} groups  (~{bands} ELO bands)")

    # Year range
    years = sorted(set(g["year"] for g in groups))
    print(f"\nYears: {years[0]}–{years[-1]}  ({len(years)} distinct years)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate orchestrator scrape groups")
    parser.add_argument(
        "--preview", action="store_true",
        help="Print statistics without writing to DB",
    )
    parser.add_argument(
        "--txt", type=Path,
        default=Path("data/players_list_foa_2026-04.txt"),
        help="Path to FIDE TXT player list",
    )
    parser.add_argument(
        "--from-year", type=int, default=2009, dest="from_year",
    )
    parser.add_argument(
        "--to-year", type=int, default=BASE_YEAR, dest="to_year",
    )
    args = parser.parse_args()

    if not args.txt.exists():
        sys.exit(f"Player list not found: {args.txt}")

    print(f"Loading players from {args.txt} ...")
    years = range(args.from_year, args.to_year + 1)
    groups = generate_groups(args.txt, years)

    print_preview(groups)

    if args.preview:
        print("\n[Preview mode — nothing written to DB]")
        return

    print("\nWriting to PostgreSQL (orchestrator.scrape_groups) ...")
    inserted, skipped = insert_groups(groups)
    print(f"Done: {inserted:,} inserted, {skipped:,} skipped (already exist)")


if __name__ == "__main__":
    main()
