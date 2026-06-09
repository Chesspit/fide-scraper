"""QC-Daten — SQL-Queries für die QC-Übersichtsseiten."""
import os
import pandas as pd
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fide:nimzo194.@localhost:5434/fidedb")


def _db():
    return psycopg2.connect(DATABASE_URL)


def _fetch(sql: str, params=None) -> pd.DataFrame:
    conn = _db()
    cur = conn.cursor()
    cur.execute(sql, params or {})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=cols)


def _group_where(group: str) -> tuple[str, dict]:
    """Gibt WHERE-Clause und params-Dict für Gruppen-Filter zurück."""
    if not group or group == "all":
        return "TRUE", {}
    if group == "swiss_2026":
        return "p.swiss_2026 = TRUE", {}
    return "p.analysis_group = %(group)s", {"group": group}


def get_federation_options() -> list[dict]:
    """Alle Föderationen mit März-2024-Korrekturen, alphabetisch."""
    df = _fetch("""
        SELECT DISTINCT p.federation
        FROM rating_corrections rc
        JOIN players p ON p.fide_id = rc.fide_id
        WHERE rc.period = '2024-03-01'
          AND p.federation IS NOT NULL
        ORDER BY p.federation
    """)
    opts = [{"label": "Alle", "value": ""}]
    for fed in df["federation"].tolist():
        opts.append({"label": fed, "value": fed})
    return opts


def get_group_options() -> list[dict]:
    """Dropdown-Optionen: Alle + analysis_group-Werte + swiss_2026."""
    df = _fetch(
        "SELECT DISTINCT analysis_group FROM players "
        "WHERE analysis_group IS NOT NULL ORDER BY analysis_group"
    )
    opts = [{"label": "Alle Gruppen", "value": "all"}]
    for g in df["analysis_group"].tolist():
        opts.append({"label": g, "value": g})
    opts.append({"label": "swiss_2026", "value": "swiss_2026"})
    return opts


# ---------------------------------------------------------------------------
# Seite 1: Jahresübersicht
# ---------------------------------------------------------------------------

def load_annual_kpis(group: str) -> dict:
    clause, params = _group_where(group)
    sql = f"""
        SELECT
            COUNT(*)                                                           AS total_windows,
            ROUND(100.0 * SUM(CASE WHEN q.flag='ok' THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1)                                    AS ok_pct,
            SUM(CASE WHEN q.flag='warn'  THEN 1 ELSE 0 END)                   AS total_warn,
            SUM(CASE WHEN q.flag='error' THEN 1 ELSE 0 END)                   AS total_error
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE {clause}
    """
    df = _fetch(sql, params)
    row = df.iloc[0] if not df.empty else {}
    return {
        "total_windows": int(row.get("total_windows", 0) or 0),
        "ok_pct":        float(row.get("ok_pct", 0) or 0),
        "total_warn":    int(row.get("total_warn", 0) or 0),
        "total_error":   int(row.get("total_error", 0) or 0),
    }


def load_annual_table(group: str) -> pd.DataFrame:
    clause, params = _group_where(group)
    sql = f"""
        SELECT
            EXTRACT(YEAR FROM q.period_end)::int                               AS jahr,
            COUNT(DISTINCT q.fide_id)                                          AS spieler,
            COUNT(*)                                                            AS fenster,
            ROUND(100.0 * SUM(CASE WHEN q.flag='ok' THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1)                                    AS ok_pct,
            SUM(CASE WHEN q.flag='warn'  THEN 1 ELSE 0 END)                   AS warn,
            SUM(CASE WHEN q.flag='error' THEN 1 ELSE 0 END)                   AS error,
            ROUND(AVG(ABS(q.delta - q.correction))::numeric, 1)               AS avg_delta_adj
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE {clause}
        GROUP BY jahr
        ORDER BY jahr DESC
    """
    return _fetch(sql, params)


# ---------------------------------------------------------------------------
# Seite 1: Monatsdetail (für gewähltes Jahr)
# ---------------------------------------------------------------------------

def load_monthly_table(year: int, group: str) -> pd.DataFrame:
    clause, params = _group_where(group)
    params["year"] = year
    sql = f"""
        SELECT
            TO_CHAR(q.period_end, 'YYYY-MM')                                   AS monat,
            q.period_end,
            COUNT(*)                                                            AS fenster,
            ROUND(100.0 * SUM(CASE WHEN q.flag='ok' THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1)                                    AS ok_pct,
            SUM(CASE WHEN q.flag='warn'  THEN 1 ELSE 0 END)                   AS warn,
            SUM(CASE WHEN q.flag='error' THEN 1 ELSE 0 END)                   AS error,
            ROUND(AVG(ABS(q.delta - q.correction))::numeric, 1)               AS avg_delta_adj
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE EXTRACT(YEAR FROM q.period_end) = %(year)s
          AND {clause}
        GROUP BY q.period_end
        ORDER BY q.period_end
    """
    return _fetch(sql, params)


def load_monthly_delta_adj(year: int, group: str) -> pd.DataFrame:
    """Lädt (fide_id, period_end, delta_adj) für Zwei-Monats-Muster-Erkennung."""
    clause, params = _group_where(group)
    params["year"] = year
    sql = f"""
        SELECT
            q.fide_id,
            q.period_end,
            (q.delta - q.correction)                                           AS delta_adj
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE EXTRACT(YEAR FROM q.period_end) = %(year)s
          AND {clause}
        ORDER BY q.fide_id, q.period_end
    """
    return _fetch(sql, params)


def detect_two_month_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Findet Spieler, bei denen Monat T und Monat T+1 entgegengesetzte Deltas
    haben, die sich im Paar aufheben (|T|≥5, |T+1|≥5, |T+T+1|≤5).
    Gibt DataFrame (period_end → pattern_players) zurück.
    """
    if df.empty:
        return pd.DataFrame(columns=["period_end", "pattern_players"])
    df = df.copy()
    df["period_end"] = pd.to_datetime(df["period_end"])
    df = df.sort_values(["fide_id", "period_end"])
    df["next_delta"] = df.groupby("fide_id")["delta_adj"].shift(-1)
    df["next_period"] = df.groupby("fide_id")["period_end"].shift(-1)
    df["months_apart"] = (
        (df["next_period"].dt.year - df["period_end"].dt.year) * 12
        + (df["next_period"].dt.month - df["period_end"].dt.month)
    )
    mask = (
        (df["months_apart"] == 1)
        & (df["delta_adj"].abs() >= 5)
        & (df["next_delta"].abs() >= 5)
        & ((df["delta_adj"] + df["next_delta"]).abs() <= 5)
        & (df["delta_adj"] * df["next_delta"] < 0)
    )
    result = (
        df[mask]
        .groupby("period_end")["fide_id"]
        .nunique()
        .reset_index(name="pattern_players")
    )
    return result


def load_annual_checksum(year: int, group: str) -> dict:
    """Jahresprüfsumme: Dez[Y-1] + Σ Partien + Σ Korrekturen = Dez[Y].
    Nur für Spieler die in qc_rating_check für dieses Jahr vorhanden sind.
    """
    clause, params = _group_where(group)
    params["year"] = year
    sql = f"""
        WITH qc_players AS (
            SELECT DISTINCT q.fide_id
            FROM qc_rating_check q
            JOIN players p USING (fide_id)
            WHERE EXTRACT(YEAR FROM q.period_end) = %(year)s
              AND {clause}
        ),
        dec_prev AS (
            SELECT rh.fide_id, rh.published_rating AS dec_prev
            FROM rating_history rh
            JOIN qc_players USING (fide_id)
            WHERE rh.period = make_date(%(year)s - 1, 12, 1)
              AND rh.published_rating IS NOT NULL
        ),
        dec_curr AS (
            SELECT rh.fide_id, rh.published_rating AS dec_curr
            FROM rating_history rh
            JOIN qc_players USING (fide_id)
            WHERE rh.period = make_date(%(year)s, 12, 1)
              AND rh.published_rating IS NOT NULL
        ),
        games_yr AS (
            SELECT g.fide_id, SUM(g.rating_change_weighted) AS game_sum
            FROM game_results g
            JOIN qc_players USING (fide_id)
            WHERE EXTRACT(YEAR FROM g.period) = %(year)s
            GROUP BY g.fide_id
        ),
        corrs_yr AS (
            SELECT rc.fide_id, SUM(rc.amount) AS corr_sum
            FROM rating_corrections rc
            JOIN qc_players USING (fide_id)
            WHERE EXTRACT(YEAR FROM rc.period) = %(year)s
            GROUP BY rc.fide_id
        )
        SELECT
            COUNT(*)                                                            AS total,
            COUNT(*) FILTER (WHERE ABS(annual_diff) <= 3)                      AS ok,
            COUNT(*) FILTER (WHERE ABS(annual_diff) > 3 AND ABS(annual_diff) <= 10) AS warn,
            COUNT(*) FILTER (WHERE ABS(annual_diff) > 10)                      AS error,
            ROUND(AVG(ABS(annual_diff))::numeric, 1)                           AS avg_diff
        FROM (
            SELECT
                ROUND((dp.dec_prev
                       + COALESCE(gy.game_sum, 0)
                       + COALESCE(cy.corr_sum, 0)
                       - dc.dec_curr)::numeric, 1) AS annual_diff
            FROM dec_prev dp
            JOIN dec_curr dc USING (fide_id)
            LEFT JOIN games_yr gy USING (fide_id)
            LEFT JOIN corrs_yr cy USING (fide_id)
        ) sub
    """
    df = _fetch(sql, params)
    row = df.iloc[0] if not df.empty else {}
    return {
        "total":    int(row.get("total", 0) or 0),
        "ok":       int(row.get("ok", 0) or 0),
        "warn":     int(row.get("warn", 0) or 0),
        "error":    int(row.get("error", 0) or 0),
        "avg_diff": float(row.get("avg_diff", 0) or 0),
    }


def load_worst_offenders(year: int, group: str) -> pd.DataFrame:
    clause, params = _group_where(group)
    params["year"] = year
    sql = f"""
        SELECT
            p.name,
            p.analysis_group                                                    AS gruppe,
            TO_CHAR(q.period_end, 'YYYY-MM')                                   AS monat,
            q.published_start,
            q.published_end,
            ROUND((q.delta - q.correction)::numeric, 1)                        AS delta_adj,
            q.flag
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE EXTRACT(YEAR FROM q.period_end) = %(year)s
          AND q.flag != 'ok'
          AND {clause}
        ORDER BY ABS(q.delta - q.correction) DESC
        LIMIT 10
    """
    return _fetch(sql, params)


# ---------------------------------------------------------------------------
# Seite 2: FIDE 2024 Korrekturen
# ---------------------------------------------------------------------------

def load_corrections_kpis(group: str) -> dict:
    clause, params = _group_where(group)
    # Kein rh-Join für KPIs — nur rating_corrections + players
    sql = f"""
        SELECT
            COUNT(DISTINCT rc.fide_id)                                          AS spieler,
            SUM(rc.amount)                                                      AS summe,
            COUNT(*) FILTER (WHERE rc.source = 'snapshot_delta')               AS snapshot_count,
            COUNT(*) FILTER (WHERE rc.source = 'formula')                      AS formula_count
        FROM rating_corrections rc
        JOIN players p USING (fide_id)
        WHERE rc.period = '2024-03-01'
          AND {clause}
    """
    df = _fetch(sql, params)
    row = df.iloc[0] if not df.empty else {}
    return {
        "spieler":        int(row.get("spieler", 0) or 0),
        "summe":          int(row.get("summe", 0) or 0),
        "snapshot_count": int(row.get("snapshot_count", 0) or 0),
        "formula_count":  int(row.get("formula_count", 0) or 0),
    }


def load_corrections_table(
    group: str,
    name_filter: str = "",
    fed_filter: str = "",
    limit: int = 500,
) -> pd.DataFrame:
    clause, params = _group_where(group)
    params["limit"] = limit

    name_clause = ""
    if name_filter and name_filter.strip():
        name_clause = "AND p.name ILIKE %(name_like)s"
        params["name_like"] = f"%{name_filter.strip()}%"

    fed_clause = ""
    if fed_filter and fed_filter.strip():
        fed_clause = "AND p.federation ILIKE %(fed_like)s"
        params["fed_like"] = f"%{fed_filter.strip()}%"

    sql = f"""
        SELECT
            p.name                                                              AS spieler,
            p.federation                                                        AS federation,
            rh_feb.published_rating                                             AS elo_vormonat,
            ROUND(COALESCE(g.partien_delta, 0)::numeric, 1)                    AS partien_delta,
            ROUND((rh_feb.published_rating
                   + COALESCE(g.partien_delta, 0))::numeric, 0)::int           AS elo_nach_partien,
            rc.amount                                                           AS korrektur,
            rh_mar.published_rating                                             AS neue_elo
        FROM rating_corrections rc
        JOIN players p ON p.fide_id = rc.fide_id
        JOIN rating_history rh_feb
            ON rh_feb.fide_id = rc.fide_id AND rh_feb.period = '2024-02-01'
        JOIN rating_history rh_mar
            ON rh_mar.fide_id = rc.fide_id AND rh_mar.period = '2024-03-01'
        LEFT JOIN (
            SELECT fide_id, SUM(rating_change_weighted) AS partien_delta
            FROM game_results
            WHERE period = '2024-03-01'
            GROUP BY fide_id
        ) g ON g.fide_id = rc.fide_id
        WHERE rc.period = '2024-03-01'
          AND {clause}
          {name_clause}
          {fed_clause}
        ORDER BY p.name ASC
        LIMIT %(limit)s
    """
    return _fetch(sql, params)


def load_corrections_distribution(group: str) -> pd.DataFrame:
    clause, params = _group_where(group)
    sql = f"""
        SELECT
            FLOOR(rh.published_rating / 100) * 100                             AS elo_band,
            rc.source,
            COUNT(*)                                                            AS anzahl,
            ROUND(AVG(rc.amount)::numeric, 1)                                  AS avg_korrektur,
            SUM(rc.amount)                                                      AS sum_korrektur
        FROM rating_corrections rc
        JOIN players p USING (fide_id)
        JOIN rating_history rh
            ON rh.fide_id = rc.fide_id AND rh.period = '2024-02-01'
        WHERE rc.period = '2024-03-01'
          AND {clause}
        GROUP BY elo_band, rc.source
        ORDER BY elo_band
    """
    return _fetch(sql, params)
