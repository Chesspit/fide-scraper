"""ARPAD Chat-Tools — sichere, parametrisierte Query-Funktionen für den Chatbot.

Reine DB-Logik, kein Anthropic-Import hier (Trennung von Query- und LLM-Layer).
Jede Funktion hier wird 1:1 von einem @beta_tool-Wrapper in data_arpad.py exponiert.
Nur SELECT-Statements; alle Nutzer-Werte über %(name)s-Platzhalter, nie String-Interpolation.
"""
import os

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fide:nimzo194.@localhost:5434/fidedb")

# Kurz — Chat-Tool-Queries laufen synchron im Request-Zyklus eines Dash-Callbacks.
# Kontrast: scraper/db.py nutzt 15 min als Runaway-Query-Netz für Batch-Jobs.
_STATEMENT_TIMEOUT_MS = 5_000


def _connect():
    return psycopg2.connect(
        DATABASE_URL,
        options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
    )


def _fetch_dicts(sql: str, params: dict | None = None) -> list[dict]:
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or {})
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def search_player(name_query: str, limit: int = 10) -> list[dict]:
    """Sucht Spieler nach Name (Teilstring) oder direkter FIDE-ID."""
    q = (name_query or "").strip()
    if not q:
        return []
    if q.lstrip("-").isdigit():
        rows = _fetch_dicts(
            "SELECT fide_id, name, federation, title, women_title, sex, std_rating, "
            "  active, analysis_group, swiss_2026 "
            "FROM players WHERE fide_id = %(fid)s LIMIT 1",
            {"fid": int(q)},
        )
    else:
        rows = _fetch_dicts(
            "SELECT fide_id, name, federation, title, women_title, sex, std_rating, "
            "  active, analysis_group, swiss_2026 "
            "FROM players WHERE name ILIKE %(q)s AND active = TRUE "
            "ORDER BY std_rating DESC NULLS LAST LIMIT %(limit)s",
            {"q": f"%{q}%", "limit": limit},
        )
    for r in rows:
        r["has_game_data"] = bool(r.get("analysis_group") or r.get("swiss_2026"))
    return rows


def get_player_rating_history(fide_id: int, year_from: int | None = None,
                               year_to: int | None = None) -> dict:
    """Liefert die monatliche Rating-Historie eines Spielers (kein active-Filter)."""
    player_rows = _fetch_dicts(
        "SELECT fide_id, name, federation, title, std_rating, active "
        "FROM players WHERE fide_id = %(fid)s",
        {"fid": fide_id},
    )
    if not player_rows:
        return {"error": f"Kein Spieler mit FIDE-ID {fide_id} gefunden."}

    clauses, params = ["fide_id = %(fid)s"], {"fid": fide_id}
    if year_from:
        clauses.append("EXTRACT(YEAR FROM period) >= %(yf)s")
        params["yf"] = year_from
    if year_to:
        clauses.append("EXTRACT(YEAR FROM period) <= %(yt)s")
        params["yt"] = year_to

    history = _fetch_dicts(
        "SELECT period, COALESCE(published_rating, std_rating) AS rating "
        f"FROM rating_history WHERE {' AND '.join(clauses)} "
        "AND COALESCE(published_rating, std_rating) IS NOT NULL "
        "ORDER BY period",
        params,
    )
    for h in history:
        h["period"] = h["period"].isoformat()

    return {
        "player": player_rows[0],
        "history": history,
        "note": None if history else "Keine Rating-Historie für diesen Zeitraum gefunden.",
    }


def get_player_game_stats(fide_id: int, opponent_sex: str | None = None,
                           year_from: int | None = None,
                           year_to: int | None = None) -> dict:
    """Liefert aggregierte Partie-Statistiken eines Spielers aus game_results."""
    player_rows = _fetch_dicts(
        "SELECT fide_id, name, federation, active FROM players WHERE fide_id = %(fid)s",
        {"fid": fide_id},
    )
    if not player_rows:
        return {"error": f"Kein Spieler mit FIDE-ID {fide_id} gefunden."}

    clauses, params = ["fide_id = %(fid)s"], {"fid": fide_id}
    if opponent_sex in ("M", "F"):
        clauses.append("opponent_sex = %(osex)s")
        params["osex"] = opponent_sex
    if year_from:
        clauses.append("EXTRACT(YEAR FROM period) >= %(yf)s")
        params["yf"] = year_from
    if year_to:
        clauses.append("EXTRACT(YEAR FROM period) <= %(yt)s")
        params["yt"] = year_to
    where = " AND ".join(clauses)

    agg = _fetch_dicts(
        f"""
        SELECT
            COUNT(*)                                              AS num_games,
            ROUND(AVG(result::numeric), 3)                        AS score_rate,
            ROUND(AVG(opponent_rating), 0)                        AS avg_opponent_rating,
            ROUND(SUM(rating_change_weighted)::numeric, 1)        AS sum_rating_change,
            COUNT(*) FILTER (WHERE color = 'W')                   AS games_as_white,
            COUNT(*) FILTER (WHERE color = 'B')                   AS games_as_black,
            COUNT(DISTINCT tournament_name)                       AS num_tournaments,
            MIN(period)                                           AS first_period,
            MAX(period)                                           AS last_period
        FROM game_results WHERE {where}
        """,
        params,
    )
    row = agg[0] if agg else {}
    if not row or not row.get("num_games"):
        return {
            "player": player_rows[0],
            "num_games": 0,
            "note": (
                "Für diesen Spieler liegen keine gescrapten Partiedaten vor — er/sie gehört "
                "nicht zum analysierten Kern-Datensatz (ca. 14.000+ Top-ELO-/Analysegruppen-/"
                "Swiss-2026-Spieler)."
            ),
        }
    for k in ("first_period", "last_period"):
        if row.get(k):
            row[k] = row[k].isoformat()
    return {"player": player_rows[0], **row}


def get_player_qc_summary(fide_id: int) -> dict:
    """Liefert eine Zusammenfassung der Datenqualitäts-Prüfungen (QC) für einen Spieler."""
    player_rows = _fetch_dicts(
        "SELECT fide_id, name FROM players WHERE fide_id = %(fid)s", {"fid": fide_id})
    if not player_rows:
        return {"error": f"Kein Spieler mit FIDE-ID {fide_id} gefunden."}

    rows = _fetch_dicts(
        """
        SELECT flag, category, COUNT(*) AS n
        FROM qc_rating_check WHERE fide_id = %(fid)s
        GROUP BY flag, category
        """,
        {"fid": fide_id},
    )
    if not rows:
        return {
            "player": player_rows[0],
            "note": "Keine QC-Prüfungen für diesen Spieler vorhanden "
                    "(kein Teil des QC-geprüften Datensatzes oder zu wenige Snapshots).",
        }
    total = sum(r["n"] for r in rows)
    by_flag: dict[str, int] = {}
    for r in rows:
        by_flag.setdefault(r["flag"], 0)
        by_flag[r["flag"]] += r["n"]
    return {
        "player": player_rows[0],
        "total_windows_checked": total,
        "by_flag": by_flag,
        "by_flag_and_category": rows,
    }
