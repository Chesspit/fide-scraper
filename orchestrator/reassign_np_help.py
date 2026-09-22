"""
Einmalige Hilfsaktion (2026-09-21): dc_newplayers_1/2 haben das P0-Tier
abgeschlossen (146/146) und laufen leer. Sie helfen stattdessen den beiden
langsamsten Welt-Backfill-Threads dc_in und dc_es.

Hängt je Quell-Thread eine feste Anzahl pending Backfill-Gruppen
(update_only=0) auf dc_newplayers_1/2 um (zu gleichen Teilen). Die Gruppen
werden gleichmäßig über die Prioritätsliste des Quell-Threads verteilt, damit
die Helfer nicht nur die kleinen Restgruppen bekommen. Keine Dopplung: jede
Gruppe hängt an genau einem Thread. Die
FIDE-Abfrage hängt nur an Föderation/Jahr/ELO-Band der Gruppe
(worker.py::get_fide_ids), nicht am Proxy-Land des Threads.

Betrifft ausschließlich status='pending' (laufende/fertige bleiben
unangetastet). Prioritäten werden nicht verändert — dc_newplayers_* haben
keine eigene Restarbeit mehr.

Ausführen (lokal über Tunnel oder im VPS-Container):
    python3 -m orchestrator.reassign_np_help --count dc_es=90 --count dc_in=50 [--dry-run]
    python3 -m orchestrator.reassign_np_help --revert
"""
import argparse
import json
from pathlib import Path

from orchestrator.setup_db import connect

SOURCE_THREADS = ("dc_in", "dc_es")
HELPER_THREADS = ("dc_newplayers_1", "dc_newplayers_2")
STATE_FILE = Path(__file__).with_name("reassign_np_help_state.json")


def _summary(cur, label: str) -> None:
    print(f"--- {label} ---")
    cur.execute(
        "SELECT thread_affinity, status, count(*), coalesce(sum(player_count), 0) "
        "FROM scrape_groups WHERE thread_affinity = ANY(%s) AND update_only=0 "
        "GROUP BY thread_affinity, status ORDER BY thread_affinity, status",
        (list(SOURCE_THREADS + HELPER_THREADS),),
    )
    for row in cur.fetchall():
        print(row)


def revert(cur) -> int:
    if not STATE_FILE.exists():
        print(f"Keine State-Datei {STATE_FILE.name} — nichts zurückzusetzen.")
        return 1
    moved: dict[str, str] = json.loads(STATE_FILE.read_text())
    for group_id, origin in moved.items():
        cur.execute(
            "UPDATE scrape_groups SET thread_affinity=%s "
            "WHERE id=%s AND status='pending' AND thread_affinity = ANY(%s)",
            (origin, int(group_id), list(HELPER_THREADS)),
        )
    print(f"{len(moved)} Gruppen zurückgesetzt (bereits gelaufene bleiben beim Helfer).")
    STATE_FILE.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--revert", action="store_true")
    parser.add_argument("--count", action="append", default=[], metavar="THREAD=N",
                        help="Anzahl umzuhängender Gruppen je Quell-Thread, z. B. dc_es=90")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()
    _summary(cur, "Vorher")

    if args.revert:
        rc = revert(cur)
        _summary(cur, "Nachher")
        conn.close()
        return rc

    counts = {}
    for spec in args.count:
        thread, _, n = spec.partition("=")
        if thread not in SOURCE_THREADS or not n.isdigit():
            parser.error(f"--count {spec!r}: erwartet {'/'.join(SOURCE_THREADS)}=<Anzahl>")
        counts[thread] = int(n)
    if not counts:
        parser.error("--count fehlt")

    plan: list[tuple[int, str, str]] = []  # (group_id, source, helper)
    for source, want in counts.items():
        cur.execute(
            "SELECT id FROM scrape_groups WHERE thread_affinity=%s "
            "AND status='pending' AND update_only=0 ORDER BY priority, id",
            (source,),
        )
        ids = [r[0] for r in cur.fetchall()]
        take = min(want, len(ids))
        # gleichmäßig über die Prioritätsliste gestreut, abwechselnd auf die Helfer
        picked = [ids[k * len(ids) // take] for k in range(take)]
        given = {t: 0 for t in HELPER_THREADS}
        for k, group_id in enumerate(picked):
            helper = HELPER_THREADS[k % len(HELPER_THREADS)]
            plan.append((group_id, source, helper))
            given[helper] += 1
        print(f"{source}: {len(ids)} pending → {take} umhängen ("
              + ", ".join(f"{t} +{n}" for t, n in given.items())
              + f"), {len(ids) - take} bleiben")

    if args.dry_run:
        print(f"\n[--dry-run] Würde {len(plan)} Gruppen umhängen, keine Änderungen.")
        conn.close()
        return 0

    STATE_FILE.write_text(json.dumps({str(gid): src for gid, src, _ in plan}))
    for group_id, source, helper in plan:
        cur.execute(
            "UPDATE scrape_groups SET thread_affinity=%s "
            "WHERE id=%s AND status='pending' AND thread_affinity=%s",
            (helper, group_id, source),
        )
    print(f"\n{len(plan)} Gruppen umgehängt (Rückweg: --revert, State in {STATE_FILE.name}).")
    _summary(cur, "Nachher")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
