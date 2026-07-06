"""Integritätsprüfung: False-Positive-Erkennung in scrape_periods / scrape_groups.

Der kritischste Fehlerfall ist NICHT die sichtbare Lücke, sondern der stille
Widerspruch: die DB behauptet, eine Periode/Gruppe sei gescrapt, obwohl die
erwarteten Rohdaten fehlen. Dieses Modul deckt solche Fälle aktiv auf.

Abgrenzung: Dies ist NICHT der Reconciliation-Check gegen die offiziellen
Elo-Listen (scripts/reconcile_ratings.py) — hier geht es rein strukturell um
"wurde der Scrape-Job wie behauptet ausgeführt und liegen die Rohdaten vor".

Benannte, unabhängige Checks (erweiterbar: neue Check-Funktion schreiben und
in CHECKS registrieren — der Kern bleibt unberührt):

    ok_without_games            status='ok', aber keine game_results-Zeile
    no_data_with_games          status='no_data', aber Partien vorhanden
    blocked_error_rows          error-/HTTP-Fehler-Zeilen, die den Retry
                                dauerhaft blockieren (get_pending_periods
                                überspringt alles, was in scrape_periods steht)
    done_groups_missing_combos  Queue-Gruppe 'done', aber Soll-Kombos fehlen
    orphan_games                Partien ohne scrape_periods-Tracking-Zeile

Alle Checks sind read-only. Jeder Finding-Dict trägt: check, severity
('hard'|'soft'), subject (menschenlesbar), fix_hint sowie check-spezifische
Detailfelder. Reine Funktionen (conn wird hineingereicht) — testbar gegen
die PG-Test-DB (tests/conftest.py::data_db).
"""

from dataclasses import dataclass
from typing import Callable

from orchestrator.sync_done_groups import valid_periods_for_year

# Sentinels der Monatsrefresh-Batches (monthly_refresh_tiers.TIERS) — deren
# Population ist dynamisch (Tier-Filter statt Federation×Band), ein
# Soll-Kombo-Audit nach dem Backfill-Muster wäre für sie nicht aussagekräftig.
_TIER_SENTINELS = ("P1", "P2", "P3")

HARD = "hard"
SOFT = "soft"


@dataclass
class CheckDef:
    id: str
    description: str
    fix_hint: str
    fn: Callable  # (conn, **kwargs) -> list[dict]


# ── Check 1: ok ohne Partien ──────────────────────────────────────────────────

def check_ok_without_games(conn, **_) -> list[dict]:
    """status='ok' verspricht einen erfolgreichen Save — aber es gibt keine
    einzige game_results-Zeile. Hart, wenn die offizielle Liste (num_games > 0)
    Partien belegt; weich, wenn num_games NULL/0 ist (parsed-but-empty möglich).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sp.fide_id, sp.period, rh.num_games
            FROM scrape_periods sp
            LEFT JOIN rating_history rh
                   ON rh.fide_id = sp.fide_id AND rh.period = sp.period
            WHERE sp.status = 'ok'
              AND NOT EXISTS (
                  SELECT 1 FROM game_results gr
                  WHERE gr.fide_id = sp.fide_id AND gr.period = sp.period
              )
            ORDER BY sp.fide_id, sp.period
        """)
        rows = cur.fetchall()
    return [
        {
            "check": "ok_without_games",
            "severity": HARD if (num_games or 0) > 0 else SOFT,
            "subject": f"fide_id={fide_id} period={period}",
            "fide_id": fide_id,
            "period": period,
            "num_games_official": num_games,
        }
        for fide_id, period, num_games in rows
    ]


# ── Check 2: no_data mit Partien ─────────────────────────────────────────────

def check_no_data_with_games(conn, **_) -> list[dict]:
    """status='no_data' behauptet eine leere Periode — aber Partien existieren."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sp.fide_id, sp.period, COUNT(gr.id)
            FROM scrape_periods sp
            JOIN game_results gr
                 ON gr.fide_id = sp.fide_id AND gr.period = sp.period
            WHERE sp.status = 'no_data'
            GROUP BY sp.fide_id, sp.period
            ORDER BY sp.fide_id, sp.period
        """)
        rows = cur.fetchall()
    return [
        {
            "check": "no_data_with_games",
            "severity": HARD,
            "subject": f"fide_id={fide_id} period={period}",
            "fide_id": fide_id,
            "period": period,
            "game_rows": n,
        }
        for fide_id, period, n in rows
    ]


# ── Check 3: dauerhaft blockierte Fehler-Zeilen ──────────────────────────────

def check_blocked_error_rows(conn, **_) -> list[dict]:
    """Zeilen, die nie erfolgreich gefetcht wurden, aber jeden Retry blockieren:
    get_pending_periods (scraper/db.py) gibt nur Kombos zurück, die noch GAR
    NICHT in scrape_periods stehen — status='error' oder no_data mit
    HTTP-Fehlerstatus (429/403) bleiben damit für immer liegen.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fide_id, period, status, http_status, scraped_at
            FROM scrape_periods
            WHERE status = 'error'
               OR (status = 'no_data' AND http_status >= 400)
            ORDER BY fide_id, period
        """)
        rows = cur.fetchall()
    return [
        {
            "check": "blocked_error_rows",
            "severity": HARD,
            "subject": f"fide_id={fide_id} period={period}",
            "fide_id": fide_id,
            "period": period,
            "status": status,
            "http_status": http_status,
            "scraped_at": scraped_at.isoformat() if scraped_at else None,
        }
        for fide_id, period, status, http_status, scraped_at in rows
    ]


# ── Check 4: done-Gruppen mit fehlenden Soll-Kombos ──────────────────────────

def check_done_groups_missing_combos(conn, threshold_pct: float = 2.0, **_) -> list[dict]:
    """Auditiert Queue-Gruppen mit status='done' gegen die Ground Truth:
    Soll = aktive Spieler im (federation, ELO-Band) × gültige Perioden des
    Gruppenjahres; Ist = deren Kombos in scrape_periods (ok|no_data).

    Rating-Drift-Caveat: Band-Zugehörigkeit wird mit HEUTIGEN Ratings
    berechnet, nicht denen zum Scrape-Zeitpunkt (dasselbe akzeptierte Muster
    wie sync_done_groups.py) — deshalb Schwellwert statt 0-Toleranz.

    Ausgenommen: P1/P2/P3-Refresh-Batches (dynamische Tier-Population) und
    update_only-Gruppen (deren Soll-Menge war "zum Claim-Zeitpunkt bereits
    gescrapte Spieler" — rückwirkend nicht reproduzierbar; ein Audit gegen
    alle aktiven Spieler würde sie systematisch als unvollständig melden).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, federation, year, elo_min, elo_max
            FROM scrape_groups
            WHERE status = 'done'
              AND federation NOT IN %s
              AND COALESCE(update_only, 0) = 0
        """, (_TIER_SENTINELS,))
        groups = cur.fetchall()

    by_year: dict[int, list[tuple]] = {}
    for g in groups:
        by_year.setdefault(g[2], []).append(g)

    findings: list[dict] = []
    with conn.cursor() as cur:
        for year, year_groups in sorted(by_year.items()):
            periods = valid_periods_for_year(year)
            if not periods:
                continue
            ids = [g[0] for g in year_groups]
            # Eine Query pro Jahr statt pro Gruppe (tunnel-freundlich):
            # Soll-Spieler und Ist-Kombos je Gruppe serverseitig aggregieren.
            cur.execute("""
                WITH g AS (
                    SELECT id, federation, elo_min, elo_max
                    FROM scrape_groups WHERE id = ANY(%s)
                ),
                members AS (
                    SELECT g.id AS group_id, p.fide_id
                    FROM g
                    JOIN players p
                      ON p.active = TRUE
                     AND p.federation = g.federation
                     AND p.std_rating BETWEEN g.elo_min AND g.elo_max
                )
                SELECT m.group_id,
                       COUNT(DISTINCT m.fide_id)  AS n_players,
                       COUNT(sp.fide_id)          AS n_scraped
                FROM members m
                LEFT JOIN scrape_periods sp
                       ON sp.fide_id = m.fide_id
                      AND sp.period = ANY(%s::date[])
                      AND sp.status IN ('ok', 'no_data')
                GROUP BY m.group_id
            """, (ids, periods))
            stats = {gid: (np, ns) for gid, np, ns in cur.fetchall()}

            for gid, fed, _, elo_min, elo_max in year_groups:
                n_players, n_scraped = stats.get(gid, (0, 0))
                expected = n_players * len(periods)
                if expected == 0:
                    continue  # keine aktiven Spieler mehr im Band — kein Urteil möglich
                missing = expected - n_scraped
                missing_pct = 100.0 * missing / expected
                if missing_pct > threshold_pct:
                    findings.append({
                        "check": "done_groups_missing_combos",
                        "severity": HARD if missing_pct > 25 else SOFT,
                        "subject": f"group={gid} {fed}/{year}/{elo_min}-{elo_max}",
                        "group_id": gid,
                        "federation": fed,
                        "year": year,
                        "elo_min": elo_min,
                        "elo_max": elo_max,
                        "expected_combos": expected,
                        "scraped_combos": n_scraped,
                        "missing_pct": round(missing_pct, 1),
                    })
    return findings


# ── Check 5: Partien ohne Tracking ───────────────────────────────────────────

def check_orphan_games(conn, **_) -> list[dict]:
    """game_results-Zeilen, deren (fide_id, period) keine scrape_periods-Zeile
    hat: Daten vorhanden, Buchführung fehlt — invers zum False Positive,
    verzerrt aber jede scrape_periods-basierte Coverage-Aussage.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT gr.fide_id, gr.period, COUNT(*)
            FROM game_results gr
            WHERE NOT EXISTS (
                SELECT 1 FROM scrape_periods sp
                WHERE sp.fide_id = gr.fide_id AND sp.period = gr.period
            )
            GROUP BY gr.fide_id, gr.period
            ORDER BY gr.fide_id, gr.period
        """)
        rows = cur.fetchall()
    return [
        {
            "check": "orphan_games",
            "severity": SOFT,
            "subject": f"fide_id={fide_id} period={period}",
            "fide_id": fide_id,
            "period": period,
            "game_rows": n,
        }
        for fide_id, period, n in rows
    ]


# ── Registry ─────────────────────────────────────────────────────────────────

CHECKS: list[CheckDef] = [
    CheckDef(
        id="ok_without_games",
        description="scrape_periods status='ok' ohne game_results-Zeile",
        fix_hint="Kombo re-scrapen: DELETE FROM scrape_periods WHERE fide_id=… AND period=…, "
                 "dann backfill.py --fide-ids … --from … --to …",
        fn=check_ok_without_games,
    ),
    CheckDef(
        id="no_data_with_games",
        description="scrape_periods status='no_data', aber Partien vorhanden",
        fix_hint="Status prüfen und ggf. auf 'ok' korrigieren oder Kombo re-scrapen",
        fn=check_no_data_with_games,
    ),
    CheckDef(
        id="blocked_error_rows",
        description="error-/HTTP-Fehler-Zeilen blockieren den Retry dauerhaft",
        fix_hint="Zeilen löschen, damit get_pending_periods sie wieder anbietet "
                 "(DELETE … WHERE status='error' OR (status='no_data' AND http_status>=400))",
        fn=check_blocked_error_rows,
    ),
    CheckDef(
        id="done_groups_missing_combos",
        description="Queue-Gruppe 'done', aber Soll-Kombos fehlen in scrape_periods",
        fix_hint="Gruppe requeuen: UPDATE scrape_groups SET status='pending' WHERE id=… "
                 "(Rating-Drift als Ursache zuerst ausschließen)",
        fn=check_done_groups_missing_combos,
    ),
    CheckDef(
        id="orphan_games",
        description="game_results ohne scrape_periods-Tracking-Zeile",
        fix_hint="Tracking nachziehen: INSERT INTO scrape_periods (fide_id, period, status) "
                 "VALUES (…, …, 'ok')",
        fn=check_orphan_games,
    ),
]

CHECK_IDS = [c.id for c in CHECKS]


def run_checks(conn, check_ids: list[str] | None = None, **kwargs) -> dict[str, list[dict]]:
    """Führt die (ausgewählten) Checks aus. Returns {check_id: findings}."""
    selected = [c for c in CHECKS if check_ids is None or c.id in check_ids]
    return {c.id: c.fn(conn, **kwargs) for c in selected}


def has_hard_findings(results: dict[str, list[dict]]) -> bool:
    return any(f["severity"] == HARD for findings in results.values() for f in findings)
