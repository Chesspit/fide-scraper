#!/usr/bin/env python3
"""Listen-Gruppen erzeugen — offene Soll-Kombos nachscrapen (update_only = 3).

Auswahl und Band-Logik: orchestrator/list_population.py (keine active-/
std_rating-Filter, erfasst auch Inaktive, Reaktivierte und Neuzugänge).

Zwei Modi:

  --years 2020-2026   Jahres-Gruppen je Föderation × Jahr × Band. Thread =
                      der Thread, der die Föderation im Welt-Backfill am
                      häufigsten hatte (passende Zeitzone + Proxy-Region);
                      Föderationen ohne Historie fallen auf einen Thread je
                      Kontinent zurück. Danach Lastausgleich nur innerhalb
                      derselben Zeitzonen-Region (THREAD_REGION). Zeitraum je
                      Jahr bis zum Vormonat (worker.valid_periods_for_year).
  --period 2026-10-01 Eine Periode, föderationsübergreifend (Sentinel 'GAP'),
                      verteilt auf --threads (Default dc_update_1).

Anlass (2026-10-21): Entscheidung vom 17.09., Inaktive nicht nachzuladen,
aufgehoben — ~547k offene Kombos seit 2020, dazu ~45k für 2026-10.

Usage:
    python orchestrator/generate_list_batches.py --years 2020-2026 [--dry-run]
    python orchestrator/generate_list_batches.py --period 2026-10-01 [--dry-run]
"""

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.generate_groups import federation_continent
from orchestrator.generate_monthly_refresh_batches import _assign_thread_affinity
from orchestrator.list_population import UPDATE_ONLY_LIST, latest_list_period, population_sql
from orchestrator.monthly_refresh_tiers import DC_UPDATE_POOL, GAP_TIER, TIER_CONTINENT
from orchestrator.setup_db import connect
from orchestrator.worker import valid_periods_for_year

# Kombos statt Spieler als Maß: bei ~260-330 Kombos/h/Thread ≈ 5-8 Std./Gruppe,
# passt in ein Tagesfenster (active_hours werden nur beim Claimen geprüft).
TARGET_COMBOS = 1800

ELO_FLOOR, ELO_CEIL = 0, 9999

# Nicht für Jahres-Gruppen: dc_update_1 hat die neueste Periode, dc_update ist alt.
EXCLUDED_THREADS = {"dc_update", *DC_UPDATE_POOL}

# Lastausgleich nur innerhalb einer Region, damit die Abrufe einer Föderation in
# einer passenden Tageszeit bleiben. Zeitzonen laut profiles.yaml.
THREAD_REGION = {
    "dc_uk": "Europa", "dc_es": "Europa", "dc_de": "Europa", "dc_dach": "Europa",
    "dc_ae": "Europa",                                   # Dubai, Balkan/Türkei
    "dc_us": "Amerika", "dc_mx": "Amerika", "dc_newplayers_1": "Amerika",
    "dc_in": "Asien", "dc_hk": "Asien", "dc_newplayers_2": "Asien",
}

CONTINENT_FALLBACK = {
    "Europe": "dc_de", "Americas": "dc_us", "Asia": "dc_hk",
    "Africa": "dc_uk", "Oceania": "dc_hk", "Other": "dc_de",
}


def load_population(cur, period_from: str, period_to: str, latest: date) -> list[tuple]:
    """[(fide_id, band_rating, offen, federation)] mit offenen Soll-Kombos."""
    cur.execute(population_sql("TRUE"), (period_from, period_to, latest))
    return cur.fetchall()


def federation_threads(cur) -> dict[str, str]:
    """Föderation → häufigster Thread ihrer Welt-Backfill-Gruppen."""
    cur.execute(
        """SELECT federation, thread_affinity, count(*) FROM scrape_groups
           WHERE COALESCE(update_only, 0) = 0 AND only_period IS NULL
             AND thread_affinity IS NOT NULL AND length(federation) = 3
           GROUP BY 1, 2"""
    )
    counts: dict[str, Counter] = defaultdict(Counter)
    for fed, thread, n in cur.fetchall():
        if thread not in EXCLUDED_THREADS:
            counts[fed][thread] += n
    return {fed: c.most_common(1)[0][0] for fed, c in counts.items()}


def build_bands(players: list[tuple[int, int]], taken: set[int],
                target: int = TARGET_COMBOS) -> list[dict]:
    """Zusammenhängende Bänder von oben nach unten, je ~target Kombos.

    players: [(band_rating, offen)]. Gleiche Ratings bleiben in einem Band.
    taken: schon belegte elo_min dieser (Föderation, Jahr) — UNIQUE-Schlüssel;
    bei Kollision wird elo_min nach unten verschoben (wie build_tier_bands).
    """
    per_rating: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for rating, offen in players:
        per_rating[rating][0] += 1
        per_rating[rating][1] += offen

    chunks: list[list[int]] = []
    current: list[int] = []
    combos = 0
    for rating in sorted(per_rating, reverse=True):
        current.append(rating)
        combos += per_rating[rating][1]
        if combos >= target:
            chunks.append(current)
            current, combos = [], 0
    if current:
        chunks.append(current)

    bands = []
    elo_max = ELO_CEIL
    for i, chunk in enumerate(chunks):
        candidate = ELO_FLOOR if i == len(chunks) - 1 else min(chunk[-1], elo_max)
        while candidate in taken:
            candidate -= 1
        taken.add(candidate)
        bands.append({
            "elo_min": candidate, "elo_max": elo_max,
            "player_count": sum(per_rating[r][0] for r in chunk),
            "combos": sum(per_rating[r][1] for r in chunk),
        })
        elo_max = candidate - 1
    return bands


def balance_by_region(groups: list[dict]) -> None:
    """LPT innerhalb jeder Region: größte Gruppen zuerst an den Thread der Region
    mit der kleinsten Kombo-Last. Threads ohne Region bleiben unverändert."""
    by_region: dict[str, list[dict]] = defaultdict(list)
    for g in groups:
        region = THREAD_REGION.get(g["thread_affinity"])
        if region:
            by_region[region].append(g)
    for region, region_groups in by_region.items():
        load = {t: 0 for t, r in THREAD_REGION.items() if r == region}
        for g in sorted(region_groups, key=lambda g: -g["combos"]):
            thread = min(load, key=load.get)
            g["thread_affinity"] = thread
            load[thread] += g["combos"]


def existing_elo_mins(cur, federation: str, year: int) -> set[int]:
    cur.execute("SELECT elo_min FROM scrape_groups WHERE federation = %s AND year = %s",
                (federation, year))
    return {r[0] for r in cur.fetchall()}


def build_year_groups(cur, years: list[int], latest: date) -> list[dict]:
    fed_thread = federation_threads(cur)
    groups = []
    for year in years:
        periods = valid_periods_for_year(year)
        if not periods:
            continue
        by_fed: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for _fid, band, offen, fed in load_population(cur, periods[0], periods[-1], latest):
            if fed and fed.strip():      # ohne Föderation findet der Worker sie nicht
                by_fed[fed.strip()].append((band, offen))
        for fed, players in sorted(by_fed.items()):
            continent = federation_continent(fed)
            thread = fed_thread.get(fed) or CONTINENT_FALLBACK[continent]
            for b in build_bands(players, existing_elo_mins(cur, fed, year)):
                groups.append({
                    "federation": fed, "continent": continent, "year": year,
                    "only_period": None, "thread_affinity": thread, **b,
                })
    balance_by_region(groups)
    return groups


def build_period_groups(cur, period: date, pool: list[str], latest: date) -> list[dict]:
    iso = period.isoformat()
    players = [(band, offen) for _f, band, offen, _fed in load_population(cur, iso, iso, latest)]
    bands = build_bands(players, existing_elo_mins(cur, GAP_TIER, period.year))
    groups = [{"federation": GAP_TIER, "continent": TIER_CONTINENT, "year": period.year,
               "only_period": period, **b} for b in bands]
    _assign_thread_affinity(groups, pool=pool)
    return groups


def assign_priorities(cur, groups: list[dict]) -> None:
    """Hinter alles Bestehende; neuestes Jahr und höchstes Band zuerst."""
    cur.execute("SELECT COALESCE(MAX(priority), 0) FROM scrape_groups")
    base = cur.fetchone()[0]
    for rank, g in enumerate(sorted(groups, key=lambda g: (-g["year"], -g["elo_min"])), start=1):
        g["priority"] = base + rank


def insert_groups(cur, groups: list[dict], note: str) -> tuple[int, int]:
    inserted = skipped = 0
    for g in groups:
        cur.execute(
            """INSERT INTO scrape_groups
                   (federation, continent, year, elo_min, elo_max, player_count, status,
                    priority, thread_affinity, update_only, only_period, notes)
               VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)
               ON CONFLICT (federation, year, elo_min) DO NOTHING""",
            (g["federation"], g["continent"], g["year"], g["elo_min"], g["elo_max"],
             g["player_count"], g["priority"], g["thread_affinity"], UPDATE_ONLY_LIST,
             g["only_period"], f"{note}: {g['combos']} Kombos"),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


def print_summary(groups: list[dict]) -> None:
    by_thread: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in groups:
        t = by_thread[g["thread_affinity"]]
        t[0] += 1
        t[1] += g["player_count"]
        t[2] += g["combos"]
    print(f"{len(groups):,} Gruppen, {sum(g['player_count'] for g in groups):,} Spieler, "
          f"{sum(g['combos'] for g in groups):,} offene Kombos")
    print(f"\n  {'Thread':<18}{'Gruppen':>8}{'Spieler':>10}{'Kombos':>10}")
    for thread, (n, players, combos) in sorted(by_thread.items(), key=lambda kv: -kv[1][2]):
        print(f"  {thread:<18}{n:>8,}{players:>10,}{combos:>10,}")


def parse_years(spec: str) -> list[int]:
    start, _, end = spec.partition("-")
    return list(range(int(start), int(end or start) + 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Listen-Gruppen (offene Soll-Kombos) erzeugen")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--years", help="Jahresbereich, z. B. 2020-2026")
    mode.add_argument("--period", help="Einzelne Periode YYYY-MM-01")
    parser.add_argument("--threads", default=",".join(DC_UPDATE_POOL),
                        help="Thread-Pool für --period (Default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht schreiben")
    args = parser.parse_args()

    conn = connect()
    with conn.cursor() as cur:
        latest = latest_list_period(cur)
        if args.period:
            period = date.fromisoformat(args.period)
            if period.day != 1:
                parser.error("--period muss ein Monatserster sein")
            pool = [t.strip() for t in args.threads.split(",") if t.strip()]
            groups = build_period_groups(cur, period, pool, latest)
            label = f"Liste {period:%Y-%m}"
        else:
            groups = build_year_groups(cur, parse_years(args.years), latest)
            label = f"Liste {args.years}"
    if not groups:
        print("Keine offenen Kombos — nichts zu tun.")
        conn.close()
        return 0

    note = f"{label} ({date.today():%d.%m.%Y})"
    print(f"{note} — neueste Liste {latest}")
    with conn.cursor() as cur:
        assign_priorities(cur, groups)
    print_summary(groups)

    if args.dry_run:
        conn.close()
        print("\n[--dry-run: nichts geschrieben]")
        return 0

    with conn, conn.cursor() as cur:
        inserted, skipped = insert_groups(cur, groups, note)
    conn.close()
    print(f"\n{inserted} eingefügt, {skipped} übersprungen (Konflikt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
