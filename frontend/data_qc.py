"""QC-Daten — SQL-Queries für die QC-Übersichtsseiten."""
import os
import pandas as pd
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fide:nimzo194.@localhost:5434/fidedb")

# Ursachen-Taxonomie — muss mit scripts/quality_check.py::CATEGORIES übereinstimmen.
# Reihenfolge = Klassifikations-Präzedenz; Label für Dropdowns/Spalten.
CATEGORY_LABELS = {
    "struktur_2008":     "Struktur 2008",
    "fehlende_perioden": "Fehlende Perioden",
    "spiegel_delta":     "Spiegel-Delta",
    "korrektur_rest":    "Korrektur-Rest",
    "k40_verdacht":      "K40-Verdacht",
    "unerklaert":        "Unerklärt",
}

CATEGORY_EXPLANATIONS = {
    "struktur_2008":     "Fenster beginnt vor 2009: Quartalsfenster, global-Gruppen ohne "
                         "Early-Scraping — strukturell, nicht behebbar.",
    "fehlende_perioden": "Mindestens ein Monat im Fenster hat keinen scrape_periods-Eintrag — "
                         "Partie-Summe ist unvollständig (Scraping-Lücke oder noch offen).",
    "spiegel_delta":     "Benachbartes Fenster mit entgegengesetztem Δadj, das sich im Paar "
                         "aufhebt — FIDE verbucht eine Korrektur über zwei Monate verteilt.",
    "korrektur_rest":    "Im Fenster liegt eine bekannte FIDE-Korrektur, aber sie erklärt das "
                         "Delta nur teilweise (Residuum über Schwelle).",
    "k40_verdacht":      "Spieler hatte K=40 in einem Fenster-Monat — Timing-Effekte bei "
                         "schnell aufsteigenden (jungen) Spielern.",
    "unerklaert":        "Keine der bekannten Ursachen greift — Kandidat für genauere Analyse.",
}


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
            SUM(CASE WHEN q.flag='error' THEN 1 ELSE 0 END)                   AS total_error,
            SUM(CASE WHEN q.category='unerklaert' THEN 1 ELSE 0 END)          AS total_unexplained
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE {clause}
    """
    df = _fetch(sql, params)
    row = df.iloc[0] if not df.empty else {}
    return {
        "total_windows":     int(row.get("total_windows", 0) or 0),
        "ok_pct":            float(row.get("ok_pct", 0) or 0),
        "total_warn":        int(row.get("total_warn", 0) or 0),
        "total_error":       int(row.get("total_error", 0) or 0),
        "total_unexplained": int(row.get("total_unexplained", 0) or 0),
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


# ---------------------------------------------------------------------------
# Seite 1: Ursachen-Kategorien (Jahr × Kategorie)
# ---------------------------------------------------------------------------

def load_category_breakdown(group: str) -> pd.DataFrame:
    """Pivot Jahr × Kategorie über alle non-ok-Fenster (Spalten = Kategorien)."""
    clause, params = _group_where(group)
    sql = f"""
        SELECT
            EXTRACT(YEAR FROM q.period_end)::int                               AS jahr,
            q.category,
            COUNT(*)                                                            AS n
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE q.flag != 'ok'
          AND q.category IS NOT NULL
          AND {clause}
        GROUP BY jahr, q.category
        ORDER BY jahr DESC
    """
    df = _fetch(sql, params)
    if df.empty:
        return pd.DataFrame(columns=["Jahr", *CATEGORY_LABELS.values(), "Summe"])
    pivot = df.pivot_table(index="jahr", columns="category", values="n",
                           aggfunc="sum", fill_value=0)
    # Feste Spaltenreihenfolge nach Taxonomie, fehlende Kategorien = 0
    for cat in CATEGORY_LABELS:
        if cat not in pivot.columns:
            pivot[cat] = 0
    pivot = pivot[list(CATEGORY_LABELS)]
    pivot["Summe"] = pivot.sum(axis=1)
    pivot = pivot.rename(columns=CATEGORY_LABELS)
    pivot = pivot.sort_index(ascending=False).reset_index().rename(columns={"jahr": "Jahr"})
    return pivot


# ---------------------------------------------------------------------------
# Seite 3: Fall-Explorer
# ---------------------------------------------------------------------------

def get_case_federation_options() -> list[dict]:
    """Föderationen mit mindestens einem non-ok-Fenster, alphabetisch."""
    df = _fetch("""
        SELECT DISTINCT p.federation
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE q.flag != 'ok' AND p.federation IS NOT NULL
        ORDER BY p.federation
    """)
    opts = [{"label": "Alle", "value": ""}]
    for fed in df["federation"].tolist():
        opts.append({"label": fed, "value": fed})
    return opts


def load_cases(
    year: int | None = None,
    category: str = "",
    flag: str = "",
    group: str = "all",
    federation: str = "",
    name_filter: str = "",
    limit: int = 500,
) -> pd.DataFrame:
    """Fall-Liste aller non-ok-Fenster, gefiltert; schlimmste zuerst."""
    clause, params = _group_where(group)
    params["limit"] = limit

    extra = []
    if year:
        extra.append("EXTRACT(YEAR FROM q.period_end) = %(year)s")
        params["year"] = year
    if category:
        extra.append("q.category = %(category)s")
        params["category"] = category
    if flag:
        extra.append("q.flag = %(flag)s")
        params["flag"] = flag
    if federation:
        extra.append("p.federation = %(federation)s")
        params["federation"] = federation
    if name_filter and name_filter.strip():
        extra.append("p.name ILIKE %(name_like)s")
        params["name_like"] = f"%{name_filter.strip()}%"
    extra_sql = (" AND " + " AND ".join(extra)) if extra else ""

    sql = f"""
        SELECT
            q.fide_id,
            p.name,
            p.federation                                                        AS fed,
            COALESCE(p.analysis_group,
                     CASE WHEN p.swiss_2026 THEN 'swiss_2026' END)             AS gruppe,
            TO_CHAR(q.period_start, 'YYYY-MM') || ' → ' ||
                TO_CHAR(q.period_end, 'YYYY-MM')                               AS fenster,
            q.published_start,
            q.published_end,
            q.expected_change,
            q.scraped_change,
            q.correction,
            ROUND((q.delta - q.correction)::numeric, 1)                        AS delta_adj,
            q.missing_periods,
            q.flag,
            q.category
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE q.flag != 'ok'
          AND {clause}
          {extra_sql}
        ORDER BY ABS(q.delta - q.correction) DESC
        LIMIT %(limit)s
    """
    return _fetch(sql, params)


def load_player_qc_detail(fide_id: int, year: int) -> dict:
    """Monatszerlegung eines Spielers für ein Jahr (generalisiert Notebook 10/11).

    Spalten = tatsächlich vorhandene Snapshot-Perioden von der letzten Periode
    ≤ 1. Jan. des Jahres bis zur letzten Periode ≤ 1. Jan. des Folgejahres —
    dadurch automatisch quartals-tauglich vor 2012.
    Zeilen: Publiziert ELO / Partien-Δ / Korrektur / Unerklärtes Δ / Kumulativ.
    """
    info_df = _fetch("""
        SELECT p.name, p.federation,
               COALESCE(p.analysis_group,
                        CASE WHEN p.swiss_2026 THEN 'swiss_2026' END) AS gruppe
        FROM players p WHERE p.fide_id = %(fid)s
    """, {"fid": fide_id})
    info = info_df.iloc[0].to_dict() if not info_df.empty else {}

    snaps = _fetch("""
        WITH bounds AS (
            SELECT
                (SELECT MAX(period) FROM rating_history
                 WHERE fide_id = %(fid)s AND published_rating IS NOT NULL
                   AND period <= make_date(%(year)s, 1, 1))     AS p_from,
                (SELECT MAX(period) FROM rating_history
                 WHERE fide_id = %(fid)s AND published_rating IS NOT NULL
                   AND period <= make_date(%(year)s + 1, 1, 1)) AS p_to
        )
        SELECT rh.period, rh.published_rating
        FROM rating_history rh, bounds b
        WHERE rh.fide_id = %(fid)s
          AND rh.published_rating IS NOT NULL
          AND rh.period >= b.p_from AND rh.period <= b.p_to
        ORDER BY rh.period
    """, {"fid": fide_id, "year": year})

    if len(snaps) < 2:
        return {"info": info, "columns": [], "rows": [], "checksum": None}

    snaps["period"] = pd.to_datetime(snaps["period"])
    p_from, p_to = snaps["period"].iloc[0], snaps["period"].iloc[-1]

    games = _fetch("""
        SELECT period, SUM(rating_change_weighted) AS game_sum
        FROM game_results
        WHERE fide_id = %(fid)s AND period > %(p_from)s AND period <= %(p_to)s
        GROUP BY period
    """, {"fid": fide_id, "p_from": p_from.date(), "p_to": p_to.date()})
    games["period"] = pd.to_datetime(games["period"]) if not games.empty else games.get("period")

    corrs = _fetch("""
        SELECT period, SUM(amount) AS corr_sum
        FROM rating_corrections
        WHERE fide_id = %(fid)s AND period > %(p_from)s AND period <= %(p_to)s
        GROUP BY period
    """, {"fid": fide_id, "p_from": p_from.date(), "p_to": p_to.date()})
    corrs["period"] = pd.to_datetime(corrs["period"]) if not corrs.empty else corrs.get("period")

    qc = _fetch("""
        SELECT period_end, flag, category
        FROM qc_rating_check
        WHERE fide_id = %(fid)s AND period_end > %(p_from)s AND period_end <= %(p_to)s
    """, {"fid": fide_id, "p_from": p_from.date(), "p_to": p_to.date()})
    qc_map = {}
    if not qc.empty:
        qc["period_end"] = pd.to_datetime(qc["period_end"])
        qc_map = {r.period_end: (r.flag, r.category) for r in qc.itertuples()}

    # Pro Fenster (prev_snap, snap]: Partien/Korrekturen aufsummieren
    cols, pub_row, game_row, corr_row, unexp_row, cum_row, flag_cols = [], [], [], [], [], [], []
    cum = 0.0
    prev_period = None
    prev_pub = None
    for r in snaps.itertuples():
        label = r.period.strftime("%Y-%m")
        cols.append(label)
        pub_row.append(int(r.published_rating))
        if prev_period is None:
            game_row.append(None); corr_row.append(None)
            unexp_row.append(None); cum_row.append(None)
            flag_cols.append("")
        else:
            in_win = lambda df_, col: (
                float(df_[(df_["period"] > prev_period) & (df_["period"] <= r.period)][col].sum())
                if not df_.empty else 0.0
            )
            g = in_win(games, "game_sum")
            c = in_win(corrs, "corr_sum")
            unexp = (r.published_rating - prev_pub) - g - c
            cum += unexp
            game_row.append(round(g, 1))
            corr_row.append(round(c, 1) if c else 0)
            unexp_row.append(round(unexp, 1))
            cum_row.append(round(cum, 1))
            flag_cols.append(qc_map.get(r.period, ("", None))[0] or "")
        prev_period, prev_pub = r.period, r.published_rating

    total_games = float(games["game_sum"].sum()) if not games.empty else 0.0
    total_corr = float(corrs["corr_sum"].sum()) if not corrs.empty else 0.0
    checksum = round(pub_row[0] + total_games + total_corr - pub_row[-1], 1)

    def _mk(name, vals):
        d = {"Zeile": name}
        d.update({c: ("" if v is None else v) for c, v in zip(cols, vals)})
        return d

    rows = [
        _mk("Publiziert ELO", pub_row),
        _mk("Partien-Δ", game_row),
        _mk("Korrektur", corr_row),
        _mk("Unerklärtes Δ", unexp_row),
        _mk("Kumulativ", cum_row),
    ]
    return {
        "info": info,
        "columns": cols,
        "rows": rows,
        "flags": dict(zip(cols, flag_cols)),
        "checksum": checksum,
        "pub_start": pub_row[0],
        "pub_end": pub_row[-1],
        "from_label": cols[0],
        "to_label": cols[-1],
    }
