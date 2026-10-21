"""Listenbasierte Spielerauswahl (scrape_groups.update_only = 3), Stand 2026-10-21.

Soll = jede Kombination (Spieler, Periode) mit Partien laut offizieller Liste
(rating_history.num_games > 0) — dieselbe Definition wie scripts/audit_data.py
Ebene 1. Eine Listen-Gruppe holt genau die Soll-Kombos ihres Zeitraums, für die
es noch keinen scrape_periods-Eintrag gibt.

Anders als alle übrigen Gruppenarten filtert sie NICHT auf players.active und
NICHT auf players.std_rating: Beide stammen aus der FOA-Liste April 2026 bzw.
der neuesten Liste und sagen nichts darüber, ob jemand in Periode P gespielt
hat. Damit erfasst sie Inaktive (aufgehört vor April 2026), Reaktivierte,
Neuzugänge und Band-/Föderationswechsler gleichermaßen.

Band = höchstes published_rating des Spielers im Zeitraum (nur Soll-Perioden).
Hängt nicht vom Scrape-Fortschritt ab, Generator und Worker sehen also dieselbe
Zuordnung, solange keine neue Liste dazukommt.

Ausgeschlossen: Spieler, die in der neuesten importierten Liste nicht mehr mit
Rating stehen ("erklärt"). Beleg: 14.024 März-2026-Erstratings, die FIDE
zurückgezogen hat — Profil "Not rated", Kalkulation "No records found".

Zeitraum: only_period gesetzt → nur diese Periode; sonst das Gruppenjahr bis
zum Cutoff aus worker.valid_periods_for_year (Vormonat). Die jeweils neueste
Periode laufen über eigene only_period-Gruppen, damit sich Jahres- und
Perioden-Gruppen nicht dieselben Kombos teilen.

Importiert von worker.py (Live-Auswahl) und generate_list_batches.py
(Gruppenbildung), damit beide Seiten nie auseinanderlaufen.
"""

from datetime import date

UPDATE_ONLY_LIST = 3


def latest_list_period(cur) -> date | None:
    """Neueste importierte Liste (Index-Scan auf idx_rating_history_period_pubrating)."""
    cur.execute("SELECT max(period) FROM rating_history WHERE published_rating > 0")
    return cur.fetchone()[0]


def population_sql(federation_filter: str) -> str:
    """SQL für (fide_id, band_rating, offene Kombos, Föderation) im Zeitraum.

    federation_filter: SQL-Fragment auf players p, z. B. "p.federation = %s"
    oder "TRUE" (Tier-Sentinel). Parameter-Reihenfolge: [Föderation...], von,
    bis, neueste Liste.
    """
    # Erst die Föderation eingrenzen, dann rating_history per PK (fide_id, period):
    # 2-3x schneller als der Weg über den period-Index (gemessen 21.10.2026).
    return f"""
        WITH fp AS MATERIALIZED (
            SELECT p.fide_id, p.federation FROM players p WHERE {federation_filter}
        )
        SELECT fp.fide_id,
               COALESCE(max(rh.published_rating), 0) AS band_rating,
               count(*) FILTER (WHERE sp.fide_id IS NULL) AS offen,
               fp.federation
        FROM fp
        JOIN rating_history rh ON rh.fide_id = fp.fide_id AND rh.period BETWEEN %s AND %s
        LEFT JOIN scrape_periods sp ON sp.fide_id = rh.fide_id AND sp.period = rh.period
        WHERE rh.num_games > 0
          AND EXISTS (SELECT 1 FROM rating_history lt
                      WHERE lt.fide_id = rh.fide_id AND lt.period = %s
                        AND lt.published_rating > 0)
        GROUP BY fp.fide_id, fp.federation
        HAVING count(*) FILTER (WHERE sp.fide_id IS NULL) > 0
    """


def soll_combos(cur, fide_ids: list[int], periods: list[str]) -> set[tuple[int, str]]:
    """Soll-Kombos (num_games > 0) dieser Spieler in diesen Perioden."""
    cur.execute(
        """SELECT fide_id, period FROM rating_history
           WHERE fide_id = ANY(%s) AND period = ANY(%s::date[]) AND num_games > 0""",
        (fide_ids, periods),
    )
    return {(r[0], r[1].isoformat()) for r in cur.fetchall()}
