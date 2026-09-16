"""Coverage-Übersicht: Was ist bereits gescrapt? — ground-truth-basiert.

Anders als das Dashboard-Reporting (queue-getrieben, orchestrator.scrape_groups)
misst dieses Modul die Abdeckung direkt an den Daten: players ⨯ scrape_periods
⨯ game_results. Drei Dimensionen, jeweils pro Jahr aufgelöst:

    coverage_by_federation      Federation/Land × Jahr
    coverage_by_analysis_group  Analysegruppe (female_top/male_control/…) × Jahr
    coverage_by_elo_band        ELO-Band, numerisch (players.std_rating) × Jahr
    coverage_by_band            Geschlecht + 50er-Band (fn_elo_group) × Jahr
    coverage_totals             eine Zeile: Gesamtabdeckung über den Zeitraum

Kennzahlen pro Zeile:
    players_active     aktive Spieler der Dimension (heutiger Stand)
    players_scraped    davon mit ≥1 ok-Periode im Jahr
    pct_players        players_scraped / players_active
    periods_attempted  ok- + no_data-Perioden im Jahr (= versuchte Kombos)
    periods_expected   players_active × gültige FIDE-Perioden des Jahres
    pct_periods        periods_attempted / periods_expected
    periods_ok         nur ok-Perioden
    games              game_results-Zeilen im Jahr

Gültige Perioden je Jahr kommen aus sync_done_groups.valid_periods_for_year
(kanonisch, basiert auf scraper.db.is_valid_fide_period — Quartale vor 2012-08
werden korrekt berücksichtigt, Zukunftsmonate gedeckelt).

Nenner-Caveat: Als aktiv gilt players.active = TRUE. orchestrator/store.py:397-414
argumentiert, dass "in der zuletzt importierten FIDE-Standardliste UND aktiv" der
sauberere Nenner waere — zumal scripts/import_rating_snapshots.py::insert_new_players()
mit ON CONFLICT DO NOTHING arbeitet und players.active fuer Bestandsspieler beim
Monatsimport nie auffrischt, das Flag also driftet. Bewusst NICHT im selben Zug
umgestellt: sonst waeren die Coverage-Zahlen vor/nach der Aenderung nicht mehr
vergleichbar. Wer das angeht, sollte es als eigene, datierte Aenderung tun.

Rating-Drift-Caveat (ELO-Band-Dimension): Band-Zugehörigkeit nach HEUTIGEM
std_rating, nicht dem historischen. Reine Funktionen (conn → list[dict]) im
Stil von store.py — testbar gegen die PG-Test-DB (conftest.py::data_db).
"""

from orchestrator.sync_done_groups import valid_periods_for_year

DEFAULT_YEAR_FROM = 2008
DEFAULT_YEAR_TO = 2026


def _expected_periods(year_from: int, year_to: int) -> dict[int, int]:
    return {y: len(valid_periods_for_year(y)) for y in range(year_from, year_to + 1)}


def _coverage_by_dimension(
    conn,
    dim_sql: str,
    dim_key: str,
    year_from: int,
    year_to: int,
    where_extra: str = "",
) -> list[dict]:
    """Generischer Kern: dim_sql ist der SELECT-Ausdruck der Dimension über
    Alias p (players). Drei serverseitige Aggregate, in Python gejoint."""
    n_periods = _expected_periods(year_from, year_to)

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT {dim_sql} AS dim, COUNT(*)
            FROM players p
            WHERE p.active = TRUE {where_extra}
            GROUP BY 1
        """)
        active = dict(cur.fetchall())

        cur.execute(f"""
            SELECT {dim_sql} AS dim,
                   EXTRACT(YEAR FROM sp.period)::int AS jahr,
                   COUNT(DISTINCT sp.fide_id) FILTER (WHERE sp.status = 'ok')  AS players_ok,
                   COUNT(*) FILTER (WHERE sp.status = 'ok')                    AS periods_ok,
                   COUNT(*) FILTER (WHERE sp.status IN ('ok','no_data'))       AS periods_attempted
            FROM scrape_periods sp
            JOIN players p ON p.fide_id = sp.fide_id
            WHERE p.active = TRUE {where_extra}
              AND sp.period >= make_date(%s, 1, 1)
              AND sp.period <  make_date(%s + 1, 1, 1)
            GROUP BY 1, 2
        """, (year_from, year_to))
        periods = {(dim, jahr): (p_ok, per_ok, per_att)
                   for dim, jahr, p_ok, per_ok, per_att in cur.fetchall()}

        cur.execute(f"""
            SELECT {dim_sql} AS dim,
                   EXTRACT(YEAR FROM gr.period)::int AS jahr,
                   COUNT(*)
            FROM game_results gr
            JOIN players p ON p.fide_id = gr.fide_id
            WHERE p.active = TRUE {where_extra}
              AND gr.period >= make_date(%s, 1, 1)
              AND gr.period <  make_date(%s + 1, 1, 1)
            GROUP BY 1, 2
        """, (year_from, year_to))
        games = {(dim, jahr): n for dim, jahr, n in cur.fetchall()}

    rows: list[dict] = []
    for dim in sorted(active, key=str):
        n_active = active[dim]
        for year in range(year_from, year_to + 1):
            expected = n_active * n_periods.get(year, 0)
            players_ok, periods_ok, periods_att = periods.get((dim, year), (0, 0, 0))
            n_games = games.get((dim, year), 0)
            if expected == 0 and periods_att == 0 and n_games == 0:
                continue  # Jahr ohne gültige Perioden und ohne Daten
            rows.append({
                dim_key: dim,
                "year": year,
                "players_active": n_active,
                "players_scraped": players_ok,
                "pct_players": round(100.0 * players_ok / n_active, 1) if n_active else 0.0,
                "periods_attempted": periods_att,
                "periods_expected": expected,
                "pct_periods": round(100.0 * periods_att / expected, 1) if expected else 0.0,
                "periods_ok": periods_ok,
                "games": n_games,
            })
    return rows


# Warum überall "std_rating > 0" statt "IS NOT NULL":
# std_rating = 0 heißt "unbewertet" — das sind 1.261.674 der 1.505.239 aktiven
# Spieler (Stand 16.09.2026). Sie werden per Entscheid nicht gescrapt (siehe
# worker.py::get_fide_ids(), never_scraped_only-Zweig), gehören also auch nicht
# in den Nenner. Mit dem alten IS-NOT-NULL-Filter meldete die ELO-Band-Dimension
# ein Band "0" mit 1,26 Mio Spielern und 10,09 Mio Soll-Perioden allein für 2026
# und druckte damit die Gesamtabdeckung von 76 % auf ~1 %.
_RATED_ONLY = "AND p.std_rating > 0"


def coverage_by_federation(conn, year_from: int = DEFAULT_YEAR_FROM,
                           year_to: int = DEFAULT_YEAR_TO,
                           rated_only: bool = True) -> list[dict]:
    """Abdeckung je Föderation × Jahr.

    rated_only=True (Default) klammert unbewertete Spieler aus. Ohne das sind
    die Nenner durchgehend von den ~1,26 Mio Ungerateten dominiert und die
    Prozentwerte praktisch bedeutungslos.
    """
    return _coverage_by_dimension(conn, "p.federation", "federation",
                                  year_from, year_to,
                                  where_extra=_RATED_ONLY if rated_only else "")


def coverage_by_analysis_group(conn, year_from: int = DEFAULT_YEAR_FROM,
                               year_to: int = DEFAULT_YEAR_TO) -> list[dict]:
    """Abdeckung je kuratierter Analysegruppe × Jahr.

    Bewusst UNVERÄNDERT gelassen (kein rated_only): Diese Dimension ist die
    historische Vergleichsachse zu den kuratierten Gruppen aus System A, ihre
    Zahlen sollen mit früheren Auswertungen vergleichbar bleiben. Achtung bei
    der Interpretation: die Gruppen sind laut docs/project_status.md 6.7 nur
    teilweise befüllt (z.B. male_control 48 von 649 gelabelt).
    """
    return _coverage_by_dimension(
        conn, "p.analysis_group", "analysis_group", year_from, year_to,
        where_extra="AND p.analysis_group IS NOT NULL",
    )


def coverage_by_elo_band(conn, band_width: int = 100,
                         year_from: int = DEFAULT_YEAR_FROM,
                         year_to: int = DEFAULT_YEAR_TO) -> list[dict]:
    """Abdeckung je ELO-Band (numerisch, frei wählbare Breite) × Jahr."""
    dim_sql = f"(FLOOR(p.std_rating / {int(band_width)}) * {int(band_width)})::int"
    return _coverage_by_dimension(
        conn, dim_sql, "elo_band", year_from, year_to,
        where_extra=_RATED_ONLY,
    )


def coverage_by_band(conn, year_from: int = DEFAULT_YEAR_FROM,
                     year_to: int = DEFAULT_YEAR_TO) -> list[dict]:
    """Abdeckung je Geschlecht+50er-Band (f_2400_2449, m_1850_1899, …) × Jahr.

    Nutzt fn_elo_group() aus migrations/017 — dieselben Bandnamen wie in den
    Notebooks, damit Coverage und Auswertung dieselbe Sprache sprechen.
    """
    return _coverage_by_dimension(
        conn, "fn_elo_group(p.std_rating, p.sex)", "band", year_from, year_to,
        where_extra=_RATED_ONLY,
    )


def coverage_totals(conn, year_from: int = 2020,
                    year_to: int = DEFAULT_YEAR_TO,
                    rated_only: bool = True) -> dict:
    """Eine Zeile: Gesamtabdeckung über den Zeitraum — die Kopfzahl fürs Dashboard.

    Keine eigene Zähllogik, nur ein Aggregat über die Jahreszeilen derselben
    Kernabfrage. players_active wird NICHT summiert (der Wert ist in jeder
    Jahreszeile derselbe heutige Bestand, Summieren würde ihn vervielfachen).
    """
    rows = _coverage_by_dimension(
        conn, "'gesamt'", "dim", year_from, year_to,
        where_extra=_RATED_ONLY if rated_only else "",
    )
    attempted = sum(r["periods_attempted"] for r in rows)
    expected = sum(r["periods_expected"] for r in rows)
    return {
        "year_from": year_from,
        "year_to": year_to,
        "players_active": rows[0]["players_active"] if rows else 0,
        "periods_attempted": attempted,
        "periods_expected": expected,
        "pct_periods": round(100.0 * attempted / expected, 1) if expected else 0.0,
        "periods_ok": sum(r["periods_ok"] for r in rows),
        "games": sum(r["games"] for r in rows),
    }
