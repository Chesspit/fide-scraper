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
    # GAP (Perioden-Reparatur, siehe unten): Abgrenzung über only_period in
    # worker.py::get_fide_ids(), nicht über ein Rating-Band.
    "GAP": "TRUE",
}

# (elo_floor, elo_ceil) je Tier — Sentinels an den Rändern, damit künftiges
# Rating-Wachstum ohne Batch-Regenerierung abgedeckt bleibt.
TIER_BOUNDS: dict[str, tuple[int, int]] = {
    "P1": (2300, 9999),
    "P2": (0, 2299),
    "P3": (0, 2299),
    "P0": (1, 9999),  # std_rating=0 (unbewertet) bewusst ausgeschlossen, siehe generate_new_entrant_batches.py
    "GAP": (1, 9999),
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

# Eigene, kleinere Zielgröße als TIER_TARGET_MIN/MAX (2026-09-01): bei P1/P2/P3
# ist Spieler ≈ Kombo (update_only prüft meist nur 1-2 fehlende Monate), bei P0
# dagegen bis zu ~12 Perioden/Spieler (voller Jahres-Backfill für nie versuchte
# Spieler) — dieselbe 2000-3000er-Zielgröße wurde dort zu 22k-27k Kombos statt
# 2-3k, ~3,5 Tage statt der beabsichtigten paar Stunden pro Batch. Wichtig auch
# wegen worker.py::scrape_group(): active_hours werden NUR beim Claimen einer
# neuen Gruppe geprüft, nicht mitten in einer laufenden — eine zu große Gruppe
# macht das tägliche Aktivfenster der neuen Threads faktisch wirkungslos.
# 200-300 Spieler × ~8-9 Perioden ≈ 1600-2700 Kombos, bei ~260-330 Kombos/h
# (beobachtet) ≈ 5-10 Std./Batch — passt in ein Tagesfenster.
NEW_ENTRANT_TARGET_MIN = 200
NEW_ENTRANT_TARGET_MAX = 300

# ── GAP: Perioden-Reparatur (2026-09-17) ──
#
# Holt für EINE Periode (scrape_groups.only_period) genau die Spieler nach, die
# laut offizieller Liste Partien hatten, aber keinen scrape_periods-Eintrag für
# diese Periode haben. Anlass: Die Liste 2024-06 war mit einer Juli-Fassung
# vertauscht, der Pre-Filter in worker.py::scrape_group() hat deshalb 22.830
# Juni-Kombos ohne Abruf als no_data markiert. Eine Föderations-Gruppe neu
# aufzurollen hätte das ganze Jahr geprüft (~10× so viele Abrufe).
# Batches erzeugt orchestrator/generate_period_repair_batches.py.
GAP_TIER = "GAP"

# ── Listen-Perioden-Gruppen in drei Stufen (14.11.2026) ──
#
# Ersetzt P1/P2/P3 im Monatslauf: generate_list_batches.py --period legt die
# Soll-Kombos des neuen Monats in drei Stufen an, Priorität in dieser
# Reihenfolge. Band = published_rating der Periode (list_population.py).
# Sentinel → (Bezeichnung, SQL auf players p, elo_floor, elo_ceil).
# Der Alt-Sentinel "GAP" (alle Föderationen, ein Topf) bleibt für bestehende
# Gruppen gültig.
DACH_FEDERATIONS: tuple[str, ...] = ("GER", "SUI", "AUT")
_DACH_SQL = ", ".join(f"'{f}'" for f in DACH_FEDERATIONS)
LIST_PERIOD_TIERS: dict[str, tuple[str, str, int, int]] = {
    "GAP1": ("stark (Elo ≥ 2300)", "TRUE", 2300, 9999),
    "GAP2": ("DACH < 2300", f"p.federation IN ({_DACH_SQL})", 0, 2299),
    "GAP3": ("Rest < 2300", f"COALESCE(p.federation, '') NOT IN ({_DACH_SQL})", 0, 2299),
}


def list_tier_of(band: int, federation: str | None) -> str:
    """Python-Zwilling zu LIST_PERIOD_TIERS (Generator); Worker nutzt das SQL."""
    if band >= 2300:
        return "GAP1"
    return "GAP2" if (federation or "").strip() in DACH_FEDERATIONS else "GAP3"


# Vierte Stufe, niedrigste Priorität: Neulinge-Vorlauf (update_only = 4, siehe
# list_population.newcomer_sql) — die Monate vor dem Erstrating, die keine
# Liste kennt. Läuft auf LIST_REST_POOL.
VORLAUF_TIER = "GAP4"

# Stufen 1+2 auf den Update-Thread (klein, sofort dran), Stufe 3 verteilt auf
# die Backfill-Threads (wie die Handverteilung vom 13.11.2026).
LIST_PRIORITY_TIERS: tuple[str, ...] = ("GAP1", "GAP2")
LIST_REST_POOL: list[str] = [
    "dc_uk", "dc_es", "dc_de", "dc_dach", "dc_ae", "dc_us", "dc_mx",
    "dc_newplayers_1", "dc_in", "dc_hk", "dc_newplayers_2",
]
