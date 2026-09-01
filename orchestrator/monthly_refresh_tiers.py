"""Single source of truth for the monthly-refresh priority tiers (P1/P2/P3).

Ersetzt die alten 4 Kategorien (UP-ELO2300/FEMALE/GER/DACH + dc_update-Rest)
durch drei nicht-überlappende, geschlechtsunabhängige Stufen:

    P1  Alle Spieler mit ELO >= 2300 (jede Föderation, jedes Geschlecht)
    P2  Alle DACH-Spieler (GER/SUI/AUT) mit ELO < 2300 (P1 deckt DACH >=2300 bereits ab)
    P3  Alle übrigen bereits gescrapten Spieler ("Rest")

Nur Spieler mit mindestens einer erfolgreich gescrapten Periode werden von
diesem monatlichen Refresh erfasst (siehe update_only-Filter in
orchestrator/worker.py::get_fide_ids()) — nie ein Vollbackfill neuer Spieler.

Importiert von orchestrator/generate_monthly_refresh_batches.py (Batch-Erzeugung)
und orchestrator/worker.py (Live-Query beim Scrapen), damit beide Seiten nie
auseinanderlaufen.
"""

TIER_FILTERS: dict[str, str] = {
    "P1": "std_rating >= 2300",
    "P2": "federation IN ('GER','SUI','AUT') AND std_rating < 2300",
    "P3": "std_rating < 2300 AND federation NOT IN ('GER','SUI','AUT')",
    # P0 (Neuzugangs-Tier, siehe unten): kein ELO-Filter — die Abgrenzung läuft
    # nicht über die Rating-Band, sondern darüber, dass noch nie versucht wurde
    # (never_scraped_only in worker.py::get_fide_ids()).
    "P0": "TRUE",
}

# (elo_floor, elo_ceil) je Tier — Sentinels an den Rändern, damit künftiges
# Rating-Wachstum ohne Batch-Regenerierung abgedeckt bleibt.
TIER_BOUNDS: dict[str, tuple[int, int]] = {
    "P1": (2300, 9999),
    "P2": (0, 2299),
    "P3": (0, 2299),
    "P0": (0, 9999),
}

TIER_CONTINENT = "GLOBAL"

# Zielgröße pro Batch — kleiner als die frühere 3000-6000er-Spanne der
# föderationsbasierten dc_update-Batches, damit ein einzelner Batch auf einem
# Thread in ein paar Stunden statt einem ganzen Arbeitstag durchläuft
# (semi_conservative-Profil: ~655 Spieler/Stunde/Thread).
TIER_TARGET_MIN = 2000
TIER_TARGET_MAX = 3000

# Pool der DC-Threads, die ausschließlich P1/P2/P3-Batches claimen (siehe
# profiles.yaml). Föderationsagnostisch — anders als die historischen
# Backfill-Threads (dc_de/dc_in/...), die weiterhin fest an Föderationen
# gebunden sind.
#
# Start bewusst mit einem einzigen Thread: die Batches werden sequenziell
# nach Priorität (P1 vor P2 vor P3, siehe build_groups() in
# generate_monthly_refresh_batches.py) abgearbeitet. Reicht ein Thread nicht
# aus, genügt es, "dc_update_2" hier zu ergänzen und einen zweiten Eintrag in
# profiles.yaml zu aktivieren — keine weiteren Code-Änderungen nötig, das
# Greedy-Load-Balancing in generate_monthly_refresh_batches.py skaliert
# automatisch auf jede Pool-Größe.
DC_UPDATE_POOL: list[str] = ["dc_update_1"]

# scrape_groups.federation nutzt diese Werte als Tier-Sentinel statt eines
# echten 3-Buchstaben-FIDE-Codes — kollisionsfrei, da FIDE-Codes nie "P1"/
# "P2"/"P3" lauten.
TIERS: tuple[str, ...] = ("P1", "P2", "P3")

# ── P0: Neuzugangs-Tier (2026-09, Phase 1.4/2 der std_rating-Aufarbeitung) ──
#
# P1/P2/P3 erfassen bewusst NUR Spieler, die schon mindestens einmal
# erfolgreich gescraped wurden (update_only=1, EXISTS-Filter) — genau
# deshalb fallen Spieler, die NIE versucht wurden, für immer durchs Raster,
# auch wenn ihre Föderations-Gruppe längst "done" ist. P0 schließt exakt
# diese Lücke: Filter ist umgekehrt (NOT EXISTS scrape_periods — irgendein
# Eintrag, nicht nur 'ok'), scrape_groups.update_only=2 als Sentinel-Wert
# (siehe worker.py::get_fide_ids()/scrape_group()).
#
# Eigener Thread-Pool statt DC_UPDATE_POOL: läuft parallel zum laufenden
# P1/P2/P3-Monatsrefresh, ohne dessen Threads zu verdrängen.
NEW_ENTRANT_POOL: list[str] = ["dc_newplayers_1", "dc_newplayers_2"]
NEW_ENTRANT_TIERS: tuple[str, ...] = ("P0",)
