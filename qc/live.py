"""Live-Einzelabfrage Spieler × Periode für den QC-Bereich im Dashboard.

Klein genug, um sie pro Klick zu rechnen (Primärschlüssel-Zugriffe), anders
als ein Audit-Lauf (qc/audit.py, gespeichert in qc_audit_runs). Prüft für eine
Kombination dieselben Fragen wie die Audit-Ebenen 1–3:

  1  Soll? (num_games > 0 laut Liste) — gescrapt? Steht der Spieler noch in
     der neuesten Liste (sonst "zurückgezogen")?
  2  Partienzahl gescrapt = Liste?
  3  Kette Liste[P] − Liste[vorher] = Σ K×Δ (+ bekannte Korrekturen) und
     Elo-Formel je Partie (nur prüfbar mit Ro aus der Summary-Zeile)
"""

from __future__ import annotations

from datetime import date

from psycopg2.extras import RealDictCursor

from core.elo import diff_cap, fide_expected
from core.periods import latest_list_period, previous_period

CHAIN_TOLERANCE = 1.0
FORMULA_TOLERANCE = 0.01


def _one(cur, sql: str, params) -> dict | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def player_period(conn, fide_id: int, period: date) -> dict:
    """Alles zu (fide_id, period) plus Bewertung; wirft nie wegen fehlender Daten."""
    prev = previous_period(period)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        player = _one(cur, "SELECT fide_id, name, federation, sex, std_rating, active "
                           "FROM players WHERE fide_id = %s", (fide_id,))
        listed = _one(cur, "SELECT published_rating, num_games, std_rating "
                           "FROM rating_history WHERE fide_id = %s AND period = %s",
                      (fide_id, period))
        listed_prev = _one(cur, "SELECT published_rating FROM rating_history "
                                "WHERE fide_id = %s AND period = %s", (fide_id, prev))
        scrape = _one(cur, "SELECT status, k_factor, no_data_reason, http_status, scraped_at "
                           "FROM scrape_periods WHERE fide_id = %s AND period = %s",
                      (fide_id, period))
        cur.execute(
            """SELECT game_index, opponent_name, opponent_rating, result, rating_change,
                      rating_change_weighted, tournament_name, tournament_start_date
               FROM game_results WHERE fide_id = %s AND period = %s
               ORDER BY game_index""",
            (fide_id, period),
        )
        games = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COALESCE(sum(amount), 0) AS s FROM rating_corrections "
                    "WHERE fide_id = %s AND period = %s", (fide_id, period))
        correction = float(cur.fetchone()["s"])
    with conn.cursor() as plain:
        latest = latest_list_period(plain)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        in_latest = True
        if latest is not None:
            in_latest = _one(cur, "SELECT 1 AS x FROM rating_history WHERE fide_id = %s "
                                  "AND period = %s AND published_rating > 0",
                             (fide_id, latest)) is not None

    findings: list[tuple[str, str]] = []   # (severity, text) — ok/info/soft/hard wie audit.py
    n_listed = (listed or {}).get("num_games") or 0
    soll = n_listed > 0
    if player is None:
        findings.append(("hard", "Spieler fehlt in players"))
    if listed is None:
        findings.append(("info", "Keine Listenzeile für diese Periode (nicht gelistet)"))
    if soll and scrape is None:
        findings.append(("info" if not in_latest else "hard",
                         "Soll-Kombo nicht gescrapt"
                         + (" — Spieler nicht mehr in der neuesten Liste (zurückgezogen)"
                            if not in_latest else "")))
    if scrape and scrape["status"] != "ok" and soll:
        findings.append(("soft", f"Status {scrape['status']} trotz {n_listed} Partien laut Liste"))
    if scrape and scrape["status"] == "ok":
        if len(games) < n_listed:
            findings.append(("hard", f"{len(games)} Partien gescrapt, {n_listed} laut Liste"))
        elif len(games) > n_listed:
            findings.append(("info", f"{len(games)} Partien gescrapt, nur {n_listed} laut Liste"))

    weighted = sum(float(g["rating_change_weighted"] or 0) for g in games)
    chain = None
    if listed and listed_prev and listed.get("published_rating") and listed_prev.get("published_rating"):
        delta = listed["published_rating"] - listed_prev["published_rating"]
        resid = round(delta - weighted - correction, 2)
        chain = {"prev_period": prev, "list_delta": delta, "sum_weighted": round(weighted, 2),
                 "correction": correction, "residual": resid,
                 "ok": abs(resid) <= CHAIN_TOLERANCE}
        if scrape and scrape["status"] == "ok" and not chain["ok"]:
            findings.append(("soft", f"Kette schließt nicht: Rest {resid:+.2f} "
                                     "(K=40/nachgewertete Turniere sind häufige Ursachen)"))

    ro = (listed or {}).get("std_rating")
    tournaments = {g["tournament_name"] for g in games}
    formula_checkable = bool(ro) and len(tournaments) == 1
    for g in games:
        g["expected"] = g["formula_ok"] = None
        if formula_checkable and g["opponent_rating"] is not None and g["result"] is not None:
            diff = ro - g["opponent_rating"]
            cap = diff_cap(g["tournament_start_date"] or period)
            if cap is not None:
                diff = max(-cap, min(cap, diff))
            g["expected"] = fide_expected(diff)
            if g["rating_change"] is not None:
                g["formula_ok"] = abs(float(g["result"]) - g["expected"]
                                      - float(g["rating_change"])) <= FORMULA_TOLERANCE
    bad = [g for g in games if g["formula_ok"] is False]
    if bad:
        findings.append(("hard", f"Elo-Formel stimmt bei {len(bad)} von {len(games)} Partien nicht"))

    if not findings:
        findings.append(("ok", "Keine Auffälligkeiten"))
    return {
        "fide_id": fide_id, "period": period, "player": player, "listed": listed,
        "listed_prev": listed_prev, "scrape": scrape, "games": games,
        "correction": correction, "latest_list": latest, "in_latest_list": in_latest,
        "soll": soll, "chain": chain, "formula_checkable": formula_checkable,
        "findings": findings,
    }
