#!/usr/bin/env python3
"""Jahresziele fürs rückwirkende Scraping in der Queue umsetzen (2026-07).

Peter hat pro Region festgelegt, wie weit der Backfill zurückgeht
(jeweils einschließlich Zieljahr):

    DACH (GER/AUT/SUI)                 → 2012
    USA/CAN/MEX + CHN/VIE/AUS          → 2018
    Rest der Welt                      → 2020

Zwei Schritte:

A) Alt-Jahre stilllegen: pending/failed-Gruppen unter dem Zieljahr →
   status='skipped' mit notes-Markierung 'Jahresziel 2026-07: …'.
   failed MUSS mit gekippt werden, sonst reaktiviert requeue_failed()
   sie beim nächsten Worker-Start. done/running bleiben unangetastet.
   Rollback jederzeit:
     UPDATE scrape_groups SET status='pending', notes=NULL
     WHERE status='skipped' AND notes LIKE 'Jahresziel 2026-07%';

B) Rest-Welt zuweisen: pending-Gruppen im Plan (Jahr >= Ziel) ohne
   thread_affinity würden nie gescrapt — das Claiming ist strikt
   thread_affinity = <dc_id> (queue_manager._try_claim_next), kein
   Fallback. Verteilung: Region + Lastausgleich — jedes Land komplett
   zum aktuell leichtesten Kandidaten-Thread seines Kontinents
   (Last = pending-player_count). Geräte-Pools (device gesetzt,
   Mac Mini/Raspi) werden nicht angefasst.

Usage (auf dem VPS):
    docker compose exec -T worker python3 orchestrator/set_backfill_targets.py --dry-run
    docker compose exec -T worker python3 orchestrator/set_backfill_targets.py
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.setup_db import connect

TARGETS = {
    2012: {"GER", "AUT", "SUI"},
    2018: {"USA", "CAN", "MEX", "CHN", "VIE", "AUS"},
}
DEFAULT_TARGET = 2020  # alle übrigen Föderationen

NOTES_PREFIX = "Jahresziel 2026-07"

# Kandidaten-Threads je Kontinent (dc_dach bleibt DACH-exklusiv,
# dc_update_1 dem P1/P2/P3-Monats-Refresh vorbehalten). Afrika liegt
# zeitzonentechnisch zwischen London und Dubai.
CONTINENT_THREADS = {
    "Europe":   ["dc_de", "dc_uk", "dc_es"],
    "Americas": ["dc_us", "dc_mx"],
    "Asia":     ["dc_in", "dc_hk", "dc_ae"],
    "Africa":   ["dc_uk", "dc_es", "dc_ae"],
    "Oceania":  ["dc_hk"],
}


def cutoff_for(federation: str) -> int:
    for year, feds in TARGETS.items():
        if federation in feds:
            return year
    return DEFAULT_TARGET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, federation, continent, year, status, thread_affinity,
               device, COALESCE(player_count, 0)
        FROM scrape_groups
        WHERE continent NOT IN ('GLOBAL', 'Other')
        """
    )
    rows = cur.fetchall()
    print(f"Länder-Gruppen in der Queue: {len(rows):,}")

    # ── Schritt A: Alt-Jahre unter Zieljahr stilllegen ─────────────────────
    skip_ids_per_cutoff: dict[int, list[int]] = defaultdict(list)
    for gid, fed, _cont, year, status, _aff, _dev, _pc in rows:
        if status in ("pending", "failed") and year < cutoff_for(fed):
            skip_ids_per_cutoff[cutoff_for(fed)].append(gid)

    print("\nSchritt A — zu skippen (Jahr unter Ziel, pending/failed):")
    for cutoff in sorted(skip_ids_per_cutoff):
        print(f"  Ziel {cutoff}: {len(skip_ids_per_cutoff[cutoff]):,} Gruppen")
    total_skip = sum(len(v) for v in skip_ids_per_cutoff.values())
    print(f"  gesamt: {total_skip:,}")

    skipped_ids = {g for ids in skip_ids_per_cutoff.values() for g in ids}

    # ── Schritt B: Lastausgleich vorbereiten ────────────────────────────────
    # Startlast = pending-player_count je Thread nach Schritt A
    load: dict[str, int] = defaultdict(int)
    for gid, fed, _cont, year, status, aff, _dev, pc in rows:
        if status == "pending" and gid not in skipped_ids and aff:
            load[aff] += pc

    # Unzugewiesene Länder im Plan (ohne Geräte-Pools) einsammeln
    fed_groups: dict[str, list[int]] = defaultdict(list)
    fed_players: dict[str, int] = defaultdict(int)
    fed_continent: dict[str, str] = {}
    device_owned = 0
    for gid, fed, cont, year, status, aff, dev, pc in rows:
        if status != "pending" or gid in skipped_ids or aff is not None:
            continue
        if year < cutoff_for(fed):
            continue
        if dev:
            device_owned += 1
            continue
        fed_groups[fed].append(gid)
        fed_players[fed] += pc
        fed_continent[fed] = cont

    print(f"\nSchritt B — zuzuweisen: {sum(len(v) for v in fed_groups.values()):,} Gruppen "
          f"({len(fed_groups)} Länder, {sum(fed_players.values()):,} Spieler)")
    if device_owned:
        print(f"  unangetastet (Geräte-Pool, device gesetzt): {device_owned:,} Gruppen")

    print("\nStartlast je Thread (pending-Spieler nach Schritt A):")
    for t in sorted(load, key=load.get, reverse=True):
        print(f"  {t:14s} {load[t]:>10,}")

    # Greedy: größte Länder zuerst, jeweils zum leichtesten Kandidaten-Thread
    assign: dict[str, list[str]] = defaultdict(list)         # thread -> [fed, ...]
    assign_ids: dict[str, list[int]] = defaultdict(list)     # thread -> [group_id, ...]
    unmapped_continent: list[str] = []
    for fed in sorted(fed_players, key=fed_players.get, reverse=True):
        candidates = CONTINENT_THREADS.get(fed_continent[fed])
        if not candidates:
            unmapped_continent.append(fed)
            continue
        target = min(candidates, key=lambda t: load[t])
        assign[target].append(fed)
        assign_ids[target].extend(fed_groups[fed])
        load[target] += fed_players[fed]

    print("\nZuweisung Länder → Threads:")
    for t in sorted(assign):
        feds = assign[t]
        players = sum(fed_players[f] for f in feds)
        print(f"  {t:14s} +{len(assign_ids[t]):>5,} Gruppen, +{players:>9,} Spieler: "
              + ", ".join(sorted(feds)))
    if unmapped_continent:
        print(f"  OHNE Kandidaten-Thread (Kontinent unbekannt): {unmapped_continent}")

    print("\nLast je Thread nach Zuweisung (pending-Spieler):")
    for t in sorted(load, key=load.get, reverse=True):
        print(f"  {t:14s} {load[t]:>10,}")

    if args.dry_run:
        print("\n[dry-run] Keine Änderungen geschrieben.")
        conn.close()
        return

    # ── Schreiben ───────────────────────────────────────────────────────────
    for cutoff, ids in skip_ids_per_cutoff.items():
        cur.execute(
            "UPDATE scrape_groups SET status='skipped', "
            "notes=%s WHERE id = ANY(%s) AND status IN ('pending','failed')",
            (f"{NOTES_PREFIX}: Backfill bis {cutoff}", ids),
        )
        print(f"skipped (Ziel {cutoff}): {cur.rowcount:,}")

    for thread, ids in assign_ids.items():
        cur.execute(
            "UPDATE scrape_groups SET thread_affinity=%s "
            "WHERE id = ANY(%s) AND status='pending' AND thread_affinity IS NULL",
            (thread, ids),
        )
        print(f"zugewiesen an {thread}: {cur.rowcount:,}")

    print("\nFertig. Queue aktualisiert.")
    conn.close()


if __name__ == "__main__":
    main()
