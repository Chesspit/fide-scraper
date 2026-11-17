#!/usr/bin/env python3
"""Migrationen einspielen und protokollieren (Review F-18). Logik: core/migrations.py.

    python scripts/migrate.py --status              # was ist eingespielt, was offen, was geändert
    python scripts/migrate.py --baseline 030        # einmalig: 001–030 als eingespielt eintragen
    python scripts/migrate.py --dry-run             # offene Migrationen nur anzeigen
    python scripts/migrate.py                       # offene Migrationen der Reihe nach einspielen
    python scripts/migrate.py --only 031            # genau eine offene Migration einspielen

Verbindung: --dsn, sonst DATABASE_URL (Eigentümer-Rolle `fide`; über den Tunnel
localhost:5434). Die Leserolle darf das nicht und soll es auch nicht.
"""

from __future__ import annotations

import argparse
import sys

import psycopg2

from core import migrations as mig
from core.config import get_database_url


def _print_status(rows) -> None:
    for m, state in rows:
        flag = {"applied": "  ok     ", "pending": "  OFFEN  ", "changed": "  GEÄNDERT"}[state]
        print(f"{flag} {m.name}")
    n_pending = sum(1 for _, s in rows if s == "pending")
    n_changed = sum(1 for _, s in rows if s == "changed")
    print(f"\n{len(rows)} Dateien, {n_pending} offen, {n_changed} nach dem Einspielen geändert")


def _has_schema(conn) -> bool:
    """Bestehende Datenbank (players da) – im Gegensatz zu einer leeren."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.players') IS NOT NULL")
        return cur.fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", help="Verbindung (Standard: DATABASE_URL)")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--status", action="store_true", help="Stand anzeigen, nichts ändern")
    grp.add_argument("--baseline", type=int, metavar="NNN",
                     help="Migrationen bis NNN als eingespielt eintragen, ohne sie auszuführen")
    grp.add_argument("--dry-run", action="store_true", help="offene Migrationen nur anzeigen")
    grp.add_argument("--only", type=int, metavar="NNN", help="nur diese offene Migration einspielen")
    args = ap.parse_args(argv)

    conn = psycopg2.connect(args.dsn or get_database_url())
    conn.autocommit = True
    try:
        if args.status:
            _print_status(mig.status(conn))
            return 0

        if args.baseline is not None:
            marked = mig.baseline(conn, args.baseline)
            for m in marked:
                print(f"  baseline {m.name}")
            print(f"{len(marked)} Dateien als eingespielt eingetragen (nicht ausgeführt)")
            return 0

        changed = [m.name for m, s in mig.status(conn) if s == "changed"]
        if changed:
            print("⚠️  Nach dem Einspielen geändert (Prüfsumme weicht ab): " + ", ".join(changed))

        todo = mig.pending(conn)
        if args.only is not None:
            todo = [m for m in todo if m.number == args.only]
            if not todo:
                print(f"Migration {args.only:03d} ist nicht offen.")
                return 1
        elif todo and not mig.applied(conn) and _has_schema(conn):
            print("Keine Migration protokolliert. Bei einer bestehenden Datenbank zuerst "
                  "--baseline NNN, sonst würden alle Dateien ab 001 erneut laufen.")
            if not args.dry_run:
                return 1

        if not todo:
            print("Nichts offen.")
            return 0
        for m in todo:
            how = "ohne Transaktion" if m.no_transaction else "in einer Transaktion"
            if args.dry_run:
                print(f"  offen    {m.name} ({how})")
                continue
            print(f"  spiele   {m.name} ({how}) …", flush=True)
            mig.ensure_table(conn)
            mig.apply(conn, m)
            print(f"  ok       {m.name}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
