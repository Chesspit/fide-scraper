#!/usr/bin/env python3
"""Nachträglich von FIDE revidierte Monatsliste mit minimalem Aufwand einspielen.

Aufruf durch scripts/monthly_update.sh, wenn die Änderungsprüfung am Korrekturtag
(FIDE_REVISION_APPLY_DAYS, Default 14 und 28) eine veränderte Liste findet.

Vergleicht die importierte Fassung (--old) mit der revidierten (--new), nur
bewertete Spieler (std_rating > 0), und ordnet jede Änderung ein:

  Rating anders, Partienzahl gleich → nur rating_history anpassen, kein Scraping
  Partienzahl anders                → Partien + Scrape-Zeile dieser Periode löschen
                                      (vorher CSV-Sicherung) → neu scrapen
  neu in der Liste                  → rating_history anlegen; mit Partien scrapen
  nicht mehr in der Liste           → rating_history-Zeile dieser Periode löschen

Danach legt monthly_update.sh per generate_list_batches.py --period die
GAP-Gruppe an; die findet genau die Kombos mit Partien laut Liste ohne
Scrape-Zeile, also die hier gelöschten plus Neuzugänge.

SPERRE: Läuft nur, wenn die Monatsprozesse durch sind — keine P1/P2/P3-Gruppe
und keine Gruppe mit only_period = Periode mehr pending/running. Sonst Exit 3
(verschoben); der nächste Korrekturtag versucht es erneut, weil die lokale
Liste unverändert bleibt.

Nach erfolgreicher Korrektur ersetzt die revidierte Fassung die lokale Datei
(die alte wandert nach data/revisions/…_ersetzt_<datum>.zip), damit die
nächste Prüfung gegen den neuen Stand vergleicht.

Exit: 0 = korrigiert (GAP-Gruppe anlegen), 3 = verschoben, 4 = nichts zu tun,
      1 = Fehler.

    python scripts/apply_list_revision.py --period 2026-11-01 \
        --old data/standard_nov26frl.zip \
        --new data/revisions/standard_nov26frl_2026-11-14.zip [--dry-run]
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

from core.db import connect
from scripts.import_rating_snapshots import import_snapshot, parse_snapshot

EXIT_APPLIED, EXIT_DEFERRED, EXIT_NOTHING = 0, 3, 4
BACKUP_DIR = Path.home() / "backups" / "fide-scraper" / "revisions"


def rated(players: list[dict]) -> dict[int, tuple[int, int | None]]:
    """{fide_id: (rating, games)} — nur Bewertete, wie upsert_rating_history."""
    return {p["fide_id"]: (p["std_rating"], p.get("std_games"))
            for p in players if p["std_rating"]}


def classify(old: dict, new: dict) -> dict[str, list[int]]:
    """Änderungen zwischen zwei Listenfassungen einordnen."""
    both = old.keys() & new.keys()
    games = lambda g: g or 0  # noqa: E731 — None (keine Spalte) wie 0
    out = {
        "rating_only": sorted(k for k in both
                              if old[k][0] != new[k][0] and games(old[k][1]) == games(new[k][1])),
        "games_up":    sorted(k for k in both if games(old[k][1]) == 0 < games(new[k][1])),
        "games_down":  sorted(k for k in both if games(new[k][1]) == 0 < games(old[k][1])),
        "games_other": sorted(k for k in both
                              if 0 < games(old[k][1]) != games(new[k][1]) > 0),
        "added":       sorted(k for k in new.keys() - old.keys() if games(new[k][1]) > 0),
        "added_nogames": sorted(k for k in new.keys() - old.keys() if games(new[k][1]) == 0),
        "removed":     sorted(old.keys() - new.keys()),
    }
    return out


def rescrape_ids(c: dict[str, list[int]]) -> list[int]:
    return sorted(set(c["games_up"] + c["games_down"] + c["games_other"] + c["added"]))


LABELS = [
    ("rating_only",   "nur Rating anders",           "rating_history anpassen"),
    ("games_up",      "Partienzahl 0 → >0",          "neu scrapen"),
    ("games_down",    "Partienzahl >0 → 0",          "Partien löschen"),
    ("games_other",   "Partienzahl sonst geändert",  "neu scrapen"),
    ("added",         "neu, mit Partien",            "anlegen + scrapen"),
    ("added_nogames", "neu, ohne Partien",           "anlegen"),
    ("removed",       "nicht mehr in der Liste",     "rating_history-Zeile löschen"),
]


def print_table(c: dict[str, list[int]]) -> None:
    print(f"  {'Fall':<28} {'Spieler':>7}  Maßnahme")
    for key, label, action in LABELS:
        print(f"  {label:<28} {len(c[key]):>7}  {action}")
    print(f"  → {len(rescrape_ids(c))} Kombos neu zu scrapen")


def open_month_groups(cur, period: str) -> int:
    cur.execute(
        """SELECT count(*) FROM orchestrator.scrape_groups
           WHERE status IN ('pending', 'running')
             AND (federation IN ('P1', 'P2', 'P3') OR only_period = %s)
             -- Vorlauf (GAP4) holt nur die Monate vor P, berührt die Liste P nicht
             AND COALESCE(update_only, 0) <> 4""",
        (period,),
    )
    return cur.fetchone()[0]


def backup_games(cur, period: str, ids: list[int]) -> Path | None:
    if not ids:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"game_results_{period[:7]}_{date.today().isoformat()}.csv"
    sql = cur.mogrify(
        "SELECT * FROM game_results WHERE period = %s AND fide_id = ANY(%s)", (period, ids)
    ).decode()
    with open(path, "w") as fh:
        cur.copy_expert(f"COPY ({sql}) TO STDOUT WITH CSV HEADER", fh)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--period", required=True, help="YYYY-MM-01")
    ap.add_argument("--old", required=True, type=Path, help="importierte Fassung (data/…)")
    ap.add_argument("--new", required=True, type=Path, help="revidierte Fassung")
    ap.add_argument("--dry-run", action="store_true", help="nur Tabelle + Sperre zeigen")
    args = ap.parse_args()
    period = args.period

    old_players, new_players = parse_snapshot(args.old), parse_snapshot(args.new)
    c = classify(rated(old_players), rated(new_players))
    print(f"Listen-Revision {period[:7]}: {args.old.name} → {args.new.name}")
    print_table(c)
    if not any(c.values()):
        print("Keine inhaltliche Änderung bei bewerteten Spielern — nichts zu tun.")
        return EXIT_NOTHING

    conn = connect()
    with conn.cursor() as cur:
        open_groups = open_month_groups(cur, period)
    if open_groups:
        print(f"VERSCHOBEN: {open_groups} Monatsgruppen (P1–P3 / {period[:7]}) noch offen — "
              "nächster Korrekturtag versucht es erneut.")
        return EXIT_DEFERRED
    if args.dry_run:
        print("[--dry-run] Sperre frei — nichts geschrieben.")
        return EXIT_NOTHING

    ids = rescrape_ids(c)
    with conn.cursor() as cur:
        backup = backup_games(cur, period, ids)
    # Erst die Liste (eigene Transaktionen im Importer), dann die Löschungen in
    # einer Transaktion. Bricht etwas ab, bleibt die lokale Liste die alte und
    # der nächste Korrekturtag wiederholt alles — jeder Schritt ist idempotent.
    import_snapshot(conn, args.new, period, force=True)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM rating_history WHERE period = %s AND fide_id = ANY(%s)",
                    (period, c["removed"]))
        n_rh = cur.rowcount
        cur.execute("DELETE FROM game_results WHERE period = %s AND fide_id = ANY(%s)",
                    (period, ids))
        n_gr = cur.rowcount
        cur.execute("DELETE FROM scrape_periods WHERE period = %s AND fide_id = ANY(%s)",
                    (period, ids))
        n_sp = cur.rowcount
    conn.close()
    print(f"Korrigiert: Liste neu eingespielt; gelöscht {n_rh} rating_history, "
          f"{n_gr} Partien, {n_sp} Scrape-Zeilen" + (f" (Sicherung {backup})" if backup else ""))

    revdir = args.old.parent / "revisions"
    revdir.mkdir(exist_ok=True)
    superseded = revdir / f"{args.old.stem}_ersetzt_{date.today().isoformat()}{args.old.suffix}"
    shutil.move(args.old, superseded)
    shutil.copy2(args.new, args.old.with_suffix(args.new.suffix))
    print(f"Lokale Liste ersetzt; alte Fassung: {superseded}")
    return EXIT_APPLIED


if __name__ == "__main__":
    sys.exit(main())
