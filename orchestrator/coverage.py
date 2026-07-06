"""Coverage-Übersicht: Was ist bereits gescrapt? — ground-truth-basiert.

Anders als das Dashboard-Reporting (queue-getrieben, orchestrator.scrape_groups)
misst dieses Modul die Abdeckung direkt an den Daten: players ⨯ scrape_periods
⨯ game_results. Drei Dimensionen, jeweils pro Jahr aufgelöst:

    coverage_by_federation      Federation/Land × Jahr
    coverage_by_analysis_group  Analysegruppe (female_top/male_control/…) × Jahr
    coverage_by_elo_band        ELO-Band (players.std_rating) × Jahr

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


def coverage_by_federation(conn, year_from: int = DEFAULT_YEAR_FROM,
                           year_to: int = DEFAULT_YEAR_TO) -> list[dict]:
    return _coverage_by_dimension(conn, "p.federation", "federation",
                                  year_from, year_to)


def coverage_by_analysis_group(conn, year_from: int = DEFAULT_YEAR_FROM,
                               year_to: int = DEFAULT_YEAR_TO) -> list[dict]:
    return _coverage_by_dimension(
        conn, "p.analysis_group", "analysis_group", year_from, year_to,
        where_extra="AND p.analysis_group IS NOT NULL",
    )


def coverage_by_elo_band(conn, band_width: int = 100,
                         year_from: int = DEFAULT_YEAR_FROM,
                         year_to: int = DEFAULT_YEAR_TO) -> list[dict]:
    dim_sql = f"(FLOOR(p.std_rating / {int(band_width)}) * {int(band_width)})::int"
    return _coverage_by_dimension(
        conn, dim_sql, "elo_band", year_from, year_to,
        where_extra="AND p.std_rating IS NOT NULL",
    )
