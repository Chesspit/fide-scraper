#!/usr/bin/env python3
"""Reset done P0 ("Neuzugänge") scrape_groups back to pending, monthly.

Ergänzt reset_monthly_refresh.py (P1/P2/P3) um das P0-Tier — bewusst NICHT
dieselbe Jahres-Rollover-Logik: P1/P2/P3 kennen immer nur "das laufende
Jahr" und ziehen bei jedem Reset unconditional year=aktuelles_Jahr nach.
P0 ist dagegen bewusst mehrjährig (aktuell 2025+2026, für Backfill-Tiefe
neuer Spieler) — ein blindes year=aktuelles_Jahr würde die älteren
P0-Jahresbänder überschreiben und ihre Historie kappen. Daher zwei
unabhängige Schritte statt einer blinden UPDATE-year-Klausel:

1. Alle federation='P0' AND status='done' Gruppen → 'pending' (Jahr bleibt
   unangetastet). get_fide_ids(never_scraped_only=True) findet beim
   nächsten Lauf jeder Gruppe automatisch nur die seither neu
   hinzugekommenen, noch nie versuchten Spieler — wer inzwischen
   irgendeine scrape_periods-Zeile hat (auch nur 1 erfolgreiche Periode),
   fällt aus dem Filter raus. Kein doppeltes Scrapen, kein Datenverlust.
2. Prüft, ob für das aktuelle Kalenderjahr überhaupt P0-Bänder existieren
   (relevant nur beim Jahreswechsel, z.B. Januar). Falls nicht, ruft
   generate_new_entrant_batches.py für dieses Jahr auf — das Skript nutzt
   ON CONFLICT DO NOTHING, bestehende Jahre (2025/2026) bleiben in jedem
   Fall unberührt.

Verwendung:
    python3 orchestrator/reset_new_entrant_refresh.py
    python3 orchestrator/reset_new_entrant_refresh.py --dry-run

Gedacht für Schritt 3 in monthly_update.sh, neben reset_monthly_refresh.py.
"""

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.monthly_refresh_tiers import NEW_ENTRANT_TIERS
from orchestrator.setup_db import connect

_TIER_PLACEHOLDERS = ",".join(["%s"] * len(NEW_ENTRANT_TIERS))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Done P0-Gruppen zurücksetzen + fehlendes aktuelles Jahr nachziehen "
                    "(Neuzugangs-Pflege, nicht den Welt-Backfill oder P1/P2/P3)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht ändern")
    args = parser.parse_args()

    conn = connect()
    cur = conn.cursor()

    cur.execute(
        f"SELECT COUNT(*) FROM scrape_groups WHERE federation IN ({_TIER_PLACEHOLDERS}) AND status = 'done'",
        NEW_ENTRANT_TIERS,
    )
    done_count = cur.fetchone()[0]

    current_year = date.today().year
    cur.execute(
        f"SELECT COUNT(*) FROM scrape_groups WHERE federation IN ({_TIER_PLACEHOLDERS}) AND year = %s",
        (*NEW_ENTRANT_TIERS, current_year),
    )
    current_year_count = cur.fetchone()[0]

    print(f"P0: {done_count} done-Gruppen gefunden. "
          f"Jahr {current_year}: {current_year_count} Gruppen vorhanden.")

    if args.dry_run:
        print("--dry-run: keine Änderungen vorgenommen.")
        conn.close()
        return 0

    if done_count:
        cur.execute(
            f"UPDATE scrape_groups SET status = 'pending', last_run_at = NULL "
            f"WHERE federation IN ({_TIER_PLACEHOLDERS}) AND status = 'done'",
            NEW_ENTRANT_TIERS,
        )
        print(f"{cur.rowcount} P0-Gruppen auf pending zurückgesetzt (Jahr unangetastet).")
    else:
        print("Keine done-Gruppen — nichts zurückzusetzen.")
    conn.close()

    if current_year_count == 0:
        print(f"Keine P0-Gruppen für {current_year} — generiere neu ...")
        subprocess.run(
            [sys.executable,
             str(Path(__file__).resolve().parent / "generate_new_entrant_batches.py"),
             "--year", str(current_year)],
            check=True,
        )
    else:
        print(f"P0-Bänder für {current_year} bereits vorhanden — nichts zu generieren.")

    print("dc_newplayers_1/2 holen Neuzugänge beim nächsten Lauf automatisch nach.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
