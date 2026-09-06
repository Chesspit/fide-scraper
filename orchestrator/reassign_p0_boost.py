"""
Einmalige Hilfsaktion (2026-09-06): dc_update_1 leiht dc_newplayers_1/2 nach
Abschluss des laufenden P1/P2/P3-Monatsrefresh für eine Weile 30 P0-Gruppen
("New Players"/Neuzugänge) ab, um dort etwas Tempo reinzubringen.

Nimmt die jeweils 15 nächsten pending-Gruppen von dc_newplayers_1 und
dc_newplayers_2 (gleicher Effekt, nur ein dritter Worker parallel — keine
Dopplung, da jede P0-Gruppe ein eigenes, nicht überlappendes ELO-Band ist)
und hängt sie auf thread_affinity='dc_update_1' um.

Priorität: dc_update_1 hat aktuell noch 4 Monatsrefresh-Batches offen
(Priorität 45-49) und danach 58 pending DACH/FRA-Weltbackfill-Gruppen
(Priorität 50-429). Damit die geliehenen P0-Gruppen direkt nach dem
Monatsrefresh und VOR dem Weltbackfill laufen:
1. Bestehende dc_update_1-Backfill-Gruppen (update_only=0, pending) um +30
   nach hinten schieben (50-429 → 80-459) — relative Reihenfolge bleibt.
2. Die 30 geliehenen P0-Gruppen auf Priorität 50-79 setzen.

Betrifft ausschließlich status='pending' Gruppen (laufende/fertige bleiben
unangetastet), analog zum Vorgehen in reassign_dach.py.

Reversibel: thread_affinity zurück auf dc_newplayers_1/2 setzen und die
Prioritäts-Verschiebung um -30 rückgängig machen (IDs werden beim Lauf
ausgegeben).

Ausführen (lokal über Tunnel oder im VPS-Container):
    python3 -m orchestrator.reassign_p0_boost [--dry-run]
"""
import argparse

from orchestrator.setup_db import connect

BORROW_PER_THREAD = 15
SOURCE_THREADS = ("dc_newplayers_1", "dc_newplayers_2")
TARGET_THREAD = "dc_update_1"
PRIO_SHIFT = 30
BORROW_PRIO_START = 50


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()

    print("--- Vorher ---")
    cur.execute(
        "SELECT thread_affinity, status, count(*), min(priority), max(priority) "
        "FROM scrape_groups WHERE thread_affinity IN (%s,%s,%s) "
        "GROUP BY thread_affinity, status ORDER BY thread_affinity, status",
        (TARGET_THREAD, *SOURCE_THREADS),
    )
    for row in cur.fetchall():
        print(row)

    # Die 15 nächsten pending Gruppen je Quell-Thread (niedrigste Priorität
    # zuerst = das, was ohnehin als Nächstes drangewesen wäre).
    borrow_ids: list[int] = []
    for thread in SOURCE_THREADS:
        cur.execute(
            "SELECT id FROM scrape_groups WHERE thread_affinity=%s AND status='pending' "
            "ORDER BY priority LIMIT %s",
            (thread, BORROW_PER_THREAD),
        )
        ids = [r[0] for r in cur.fetchall()]
        print(f"{thread}: leihe {len(ids)} Gruppen aus (IDs {ids})")
        borrow_ids.extend(ids)

    if args.dry_run:
        print(f"\n[--dry-run] Würde {len(borrow_ids)} Gruppen umhängen, keine Änderungen.")
        conn.close()
        return 0

    # 1) Bestehende dc_update_1-Backfill-Gruppen nach hinten schieben.
    cur.execute(
        "UPDATE scrape_groups SET priority = priority + %s "
        "WHERE thread_affinity=%s AND update_only=0 AND status='pending'",
        (PRIO_SHIFT, TARGET_THREAD),
    )
    print(f"\n{cur.rowcount} bestehende dc_update_1-Backfill-Gruppen um +{PRIO_SHIFT} verschoben.")

    # 2) Geliehene P0-Gruppen umhängen + neue Priorität (50..79, Reihenfolge
    #    wie eingesammelt, also je 15 im Wechsel dc_newplayers_1/2).
    for rank, group_id in enumerate(borrow_ids):
        cur.execute(
            "UPDATE scrape_groups SET thread_affinity=%s, priority=%s "
            "WHERE id=%s AND status='pending'",
            (TARGET_THREAD, BORROW_PRIO_START + rank, group_id),
        )
    print(f"{len(borrow_ids)} P0-Gruppen auf {TARGET_THREAD} umgehängt "
          f"(Priorität {BORROW_PRIO_START}-{BORROW_PRIO_START + len(borrow_ids) - 1}).")

    print("\n--- Nachher ---")
    cur.execute(
        "SELECT thread_affinity, status, count(*), min(priority), max(priority) "
        "FROM scrape_groups WHERE thread_affinity IN (%s,%s,%s) "
        "GROUP BY thread_affinity, status ORDER BY thread_affinity, status",
        (TARGET_THREAD, *SOURCE_THREADS),
    )
    for row in cur.fetchall():
        print(row)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
