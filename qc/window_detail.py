"""QC-Fenster im Detail — Abfragen für die Analyse-Notebooks 08, 10 und 11.

Vorher lag das SQL inline in den Notebooks (10/11 mit per f-String
eingesetzten ID-Listen). Die Notebooks rufen jetzt nur noch diese Funktionen
auf (Analysen → qc ist erlaubt, eigenes QC-SQL nicht).

Alle Funktionen nehmen die Verbindung des Aufrufers (Notebooks:
notebooks/_setup.get_conn, Leserolle) und liefern DataFrames. Nur lesend.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence

import pandas as pd

ELO_GROUPS = ("ELO >= 2400", "ELO 2000-2400", "ELO < 2000")
NO_GROUP = "ohne Gruppe"


def _read(conn, sql: str, params=None) -> pd.DataFrame:
    with warnings.catch_warnings():
        # pandas warnt bei psycopg2-Verbindungen (kein SQLAlchemy) — hier gewollt
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql_query(sql, conn, params=params)


# ── Notebook 08: Delta-Verteilung je Fenster ─────────────────────────────────

def windows_with_elo_group(conn, since: str = "2015-01-01",
                           anchor: str = "2023-01-01") -> pd.DataFrame:
    """QC-Fenster ab `since` mit ELO-Gruppe nach Rating zum Stichtag `anchor`.

    Rating zum Stichtag = letzter Eintrag in rating_history bis `anchor`
    (std_rating vor published_rating), sonst players.std_rating.
    Spalten: fide_id, name, rating_anchor, elo_group, period ('YY-MM'),
    period_start, expected_change, scraped_change, delta, flag.
    """
    df = _read(conn, """
        WITH anchor AS (
            SELECT DISTINCT ON (fide_id)
                   fide_id, COALESCE(std_rating, published_rating) AS rating_anchor
            FROM rating_history
            WHERE period <= %(anchor)s
              AND COALESCE(std_rating, published_rating) IS NOT NULL
            ORDER BY fide_id, period DESC
        )
        SELECT q.fide_id, p.name,
               COALESCE(a.rating_anchor, p.std_rating) AS rating_anchor,
               CASE
                   WHEN COALESCE(a.rating_anchor, p.std_rating) >= 2400 THEN 'ELO >= 2400'
                   WHEN COALESCE(a.rating_anchor, p.std_rating) >= 2000 THEN 'ELO 2000-2400'
                   ELSE 'ELO < 2000'
               END AS elo_group,
               TO_CHAR(q.period_start, 'YY-MM') AS period,
               q.period_start, q.expected_change, q.scraped_change, q.delta, q.flag
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        LEFT JOIN anchor a USING (fide_id)
        WHERE q.period_start >= %(since)s
        ORDER BY q.fide_id, q.period_start
    """, {"since": since, "anchor": anchor})
    df["period_start"] = pd.to_datetime(df["period_start"])
    for col in ("expected_change", "scraped_change", "delta"):
        df[col] = pd.to_numeric(df[col])
    df["abs_delta"] = df["delta"].abs()
    return df


# ── Notebooks 10/11: Spieler mit Abweichungen in einem Jahr ──────────────────

def players_with_findings(conn, year: int) -> pd.DataFrame:
    """Spieler mit warn/error-Fenstern, deren Fenster im Jahr `year` endet.

    Spalten: fide_id, name, analysis_group (NULL → 'ohne Gruppe'), has_error.
    """
    df = _read(conn, """
        SELECT q.fide_id, p.name, p.analysis_group,
               bool_or(q.flag = 'error') AS has_error
        FROM qc_rating_check q
        JOIN players p USING (fide_id)
        WHERE EXTRACT(YEAR FROM q.period_end) = %(year)s
          AND q.flag IN ('warn', 'error')
        GROUP BY q.fide_id, p.name, p.analysis_group
        ORDER BY p.analysis_group, p.name
    """, {"year": year})
    df["analysis_group"] = df["analysis_group"].fillna(NO_GROUP)
    return df


def published_ratings(conn, fide_ids: Sequence[int], periods: Sequence[str]) -> pd.DataFrame:
    """published_rating je (fide_id, period) für die gegebenen Perioden (ISO-Daten)."""
    df = _read(conn, """
        SELECT fide_id, period, published_rating
        FROM rating_history
        WHERE fide_id = ANY(%(ids)s) AND period = ANY(%(periods)s::date[])
          AND published_rating IS NOT NULL
    """, {"ids": list(map(int, fide_ids)), "periods": list(periods)})
    df["period"] = pd.to_datetime(df["period"])
    return df


def game_deltas(conn, fide_ids: Sequence[int], periods: Sequence[str]) -> pd.DataFrame:
    """Σ rating_change_weighted (K×Δ) je (fide_id, period), auf 0,1 gerundet."""
    df = _read(conn, """
        SELECT fide_id, period, ROUND(SUM(rating_change_weighted)::numeric, 1) AS game_delta
        FROM game_results
        WHERE fide_id = ANY(%(ids)s) AND period = ANY(%(periods)s::date[])
        GROUP BY fide_id, period
    """, {"ids": list(map(int, fide_ids)), "periods": list(periods)})
    df["period"] = pd.to_datetime(df["period"])
    df["game_delta"] = pd.to_numeric(df["game_delta"])
    return df


def corrections(conn, fide_ids: Sequence[int], period: str = "2024-03-01") -> pd.DataFrame:
    """Bekannte FIDE-Korrekturen (rating_corrections) einer Periode, Spalte fide_corr."""
    df = _read(conn, """
        SELECT fide_id, SUM(amount) AS fide_corr
        FROM rating_corrections
        WHERE fide_id = ANY(%(ids)s) AND period = %(period)s
        GROUP BY fide_id
    """, {"ids": list(map(int, fide_ids)), "period": period})
    df["fide_corr"] = pd.to_numeric(df["fide_corr"])
    return df


def month_starts(start: str, end: str) -> list[str]:
    """Monatsersten von start bis end (inklusive) als ISO-Strings."""
    return [d.date().isoformat() for d in pd.date_range(start, end, freq="MS")]
