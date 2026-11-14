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
zum Cutoff aus core.periods.valid_periods_for_year (Vormonat). Die jeweils neueste
Periode laufen über eigene only_period-Gruppen, damit sich Jahres- und
Perioden-Gruppen nicht dieselben Kombos teilen.

latest_list_period und die Bedingung "steht in der neuesten Liste" liegen in
core/periods.py (auch von qc/audit.py genutzt).

Importiert von worker.py (Live-Auswahl) und generate_list_batches.py
(Gruppenbildung), damit beide Seiten nie auseinanderlaufen.
"""

from core.periods import in_latest_list_sql

UPDATE_ONLY_LIST = 3


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
          AND {in_latest_list_sql("rh")}
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


# ── Neulinge-Vorlauf (update_only = 4, Stufe GAP4, 14.11.2026) ──
#
# Wer in Periode P zum ersten Mal mit Rating in der Liste steht, hat die Partien
# für dieses Erstrating meist in den Monaten davor gespielt — dort hat er keinen
# Listeneintrag, die Listen-Gruppen (Soll = num_games > 0) holen sie also nie.
# Auswertung Nov. 2026: 3.765 von 49.782 solcher Abrufe hatten Partien, davon
# 88 % in den 6 Monaten vor dem Erstrating. Bis 14.11. holte das P3 nebenbei.
UPDATE_ONLY_VORLAUF = 4
VORLAUF_MONTHS = 6


def vorlauf_periods(period: str, months: int = VORLAUF_MONTHS) -> list[str]:
    """Die gültigen FIDE-Perioden der `months` Monate vor `period`, älteste zuerst."""
    from datetime import date

    from core.periods import is_valid_fide_period

    d = date.fromisoformat(period)
    out = []
    y, m = d.year, d.month
    for _ in range(months):
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        p = date(y, m, 1)
        if is_valid_fide_period(p):
            out.append(p.isoformat())
    return sorted(out)


def newcomer_sql() -> str:
    """SQL für (fide_id, band_rating, offene Vorlauf-Kombos, Föderation).

    Neuling = published_rating > 0 in P, aber in keiner früheren Liste mit
    Rating. Band = Rating in P. Offen = Vorlauf-Monate ohne scrape_periods-Eintrag.
    Parameter: siehe newcomer_params().
    """
    # Zweistufig, gemessen 14.11.2026: die volle Historienprüfung für alle
    # ~570k Spieler der Liste dauerte > 5 Min. Erst Kandidaten = in P mit
    # Rating, in der Vorperiode nicht (~4.400 im Nov.), dann nur für diese die
    # Historie per LIMIT-1-Lookup. Beide Perioden einzeln materialisiert und per
    # Hash abgeglichen (4,5 s) — als NOT EXISTS verschätzte sich der Planer
    # (> 4 Min.).
    return f"""
        WITH cur_p AS MATERIALIZED (
            SELECT fide_id, published_rating FROM rating_history
            WHERE period = %s AND published_rating > 0
        ),
        prev_p AS MATERIALIZED (
            SELECT fide_id FROM rating_history
            WHERE period = %s AND published_rating > 0
        ),
        cand AS MATERIALIZED (
            SELECT cur_p.fide_id, cur_p.published_rating
            FROM cur_p LEFT JOIN prev_p USING (fide_id)
            WHERE prev_p.fide_id IS NULL
        ),
        nc AS MATERIALIZED (
            SELECT c.fide_id, c.published_rating
            FROM cand c
            WHERE (SELECT 1 FROM rating_history e
                   WHERE e.fide_id = c.fide_id AND e.period < %s
                     AND e.published_rating > 0 LIMIT 1) IS NULL
              AND {in_latest_list_sql("c")}
        )
        SELECT nc.fide_id,
               nc.published_rating AS band_rating,
               count(*) FILTER (WHERE sp.fide_id IS NULL) AS offen,
               p.federation
        FROM nc
        JOIN players p ON p.fide_id = nc.fide_id
        CROSS JOIN unnest(%s::date[]) AS v(period)
        LEFT JOIN scrape_periods sp ON sp.fide_id = nc.fide_id AND sp.period = v.period
        GROUP BY nc.fide_id, nc.published_rating, p.federation
        HAVING count(*) FILTER (WHERE sp.fide_id IS NULL) > 0
    """


def newcomer_params(period: str, latest) -> tuple:
    vorlauf = vorlauf_periods(period)
    previous = vorlauf[-1] if vorlauf else period
    return (period, previous, period, latest, vorlauf)
