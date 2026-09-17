"""Datenprüfung ab Stichtag: Ist alles da, und ergibt die Elo-Entwicklung Sinn?

Maßstab ist die offizielle FIDE-Liste (rating_history.published_rating /
num_games), nicht die Scraping-Queue und nicht der heutige Aktiv-Status:
Soll ist jede Kombination (Spieler, Periode), für die FIDE in der Liste
mindestens eine gewertete Partie ausweist. Heute inaktive Spieler zählen mit.

Vier Ebenen, jede baut auf der vorigen auf:

    0  Referenz      Liegt die offizielle Liste für jede Periode vor?
    1  Vollständig   Wurde jede Soll-Kombination gescrapt?
    2  Partien       Stimmt die Partienzahl mit der Liste überein?
                     (+ strukturelle Checks aus integrity.py, zeitlich gefiltert)
    3  Plausibel     Liste[P] − Liste[vorher] = Σ K×Δ (Kette), Elo-Formel je
                     Partie, Wertebereiche, Ro gegen Vorliste, Rating-Sprünge

Warum Kette und Ro statistisch bewertet werden statt pro Fenster hart:
Stichprobe 2025-10 (Perioden mit exakt passender Partienzahl) — 81 % der
Fenster schließen auf ±1, die Reste häufen sich fast vollständig bei K=40
und dort, wo das Ro der Berechnungsseite nicht der Vorliste entspricht
(nachträglich gewertete Turniere, FIDE-seitige Neuberechnung). Das ist
FIDE-Realität, kein Scraping-Fehler. Hart wird es erst, wenn der Anteil
UNERKLÄRTER Fenster eine Schwelle übersteigt — das deckt systemische Brüche
(Parser, Endpoint-Umbau wie am 2026-07-14) zuverlässig auf.

Die Elo-Formel dagegen ist exakt: 509.691 Partien aus fünf Perioden, 0
Abweichungen (siehe migrations/018_fide_expected_score.sql). Geprüft werden
nur Perioden mit genau einem Turnier, weil die Summary-Zeile nur EIN Ro
liefert.

Performance: pro Periode zwei serverseitig aggregierende Abfragen, nach
Python kommen nur Zähler je (Föderation, Kategorie) plus eine Stichprobe.
Perioden laufen parallel über mehrere Verbindungen (jobs).

Alle Abfragen sind read-only (Session readonly=True).
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from orchestrator.integrity import (
    HARD,
    SOFT,
    check_blocked_error_rows,
    check_no_data_with_games,
    check_ok_without_games,
    check_orphan_games,
)
from orchestrator.sync_done_groups import valid_periods_for_year
from scraper.db import is_valid_fide_period

logger = logging.getLogger(__name__)

OK = "ok"
INFO = "info"
_SEVERITY_RANK = {OK: 0, INFO: 1, SOFT: 2, HARD: 3}

LAYERS = (0, 1, 2, 3)

DEFAULT_CHAIN_TOLERANCE = 1.0        # Elo-Punkte, Rundungsrauschen
DEFAULT_CHAIN_THRESHOLD_PCT = 2.0    # Anteil unerklärter Fenster je Periode
DEFAULT_FORMULA_THRESHOLD_PCT = 0.1  # Anteil Formel-Abweichungen je Periode
# Liste und Berechnungsseite weichen auch bei FIDE selbst ab (Stichprobe
# 2026-09-16: FIDE zeigt 7 statt der gelisteten 8 Partien bzw. "No records"
# trotz Listeneintrag; Nachbarperioden gleichen das nur in ~3 % aus). Hart
# wird es deshalb erst bei einem Ausreißer je Periode, wie ihn der
# Endpoint-Umbau vom 2026-07-14 erzeugt hat.
DEFAULT_NO_DATA_THRESHOLD_PCT = 1.0  # no_data trotz Liste, Anteil der versuchten Kombos
DEFAULT_FEWER_THRESHOLD_PCT = 3.0    # weniger Partien als gelistet, Anteil der ok-Kombos
DEFAULT_JUMP = 150                   # |Δ Liste| in einer Periode
REF_MIN_RATIO = 0.5                  # Liste gilt als unvollständig unter 50 % des Nachbar-Medians
STATEMENT_TIMEOUT = "15min"


# ── Elo-Formel (Python-Zwilling zu migrations/018) ────────────────────────────

# Obergrenzen |Δ| je Erwartungswert 0.50, 0.51, …, 0.99; darüber 1.00.
_FIDE_UPPER = (
    3, 10, 17, 25, 32, 39, 46, 53, 61, 68, 76, 83, 91, 98, 106, 113, 121, 129,
    137, 145, 153, 162, 170, 179, 188, 197, 206, 215, 225, 235, 245, 256, 267,
    278, 290, 302, 315, 328, 344, 357, 374, 391, 411, 432, 456, 484, 517, 559,
    619, 735,
)


def fide_expected(diff: int) -> float:
    """FIDE-Erwartungswert für diff = Ro − Gegner (ohne Kappung).

    Ganzzahlige Hundertstel statt 0.5 + i/100: letzteres liefert
    0.5700000000000001 und scheitert an jedem Gleichheitsvergleich.
    """
    a = abs(diff)
    hundredths = 100
    for i, upper in enumerate(_FIDE_UPPER):
        if a <= upper:
            hundredths = 50 + i
            break
    if diff < 0:
        hundredths = 100 - hundredths
    return hundredths / 100


def diff_cap(tournament_start: date) -> int | None:
    """400-Punkte-Regel nach Turnierbeginn: keine Kappung für Turniere mit
    Beginn 2022-01 bis 2024-02 (aus Daten abgeleitet, siehe Migration 018)."""
    if date(2022, 1, 1) <= tournament_start < date(2024, 3, 1):
        return None
    return 400


# ── Perioden ──────────────────────────────────────────────────────────────────

def parse_period(value: str) -> date:
    """'2020-01' oder '2020-01-01' → date(2020, 1, 1)."""
    parts = value.strip().split("-")
    if len(parts) < 2:
        raise ValueError(f"Periode erwartet als YYYY-MM: {value!r}")
    return date(int(parts[0]), int(parts[1]), 1)


def audit_periods(since: date, until: date | None = None) -> list[date]:
    """Gültige FIDE-Perioden im Zeitraum, gedeckelt auf den Vormonat.

    Der Deckel (aus valid_periods_for_year) sorgt dafür, dass eine gerade
    importierte Liste nicht als "nicht gescrapt" auffällt — der Monatslauf
    braucht deshalb kein eigenes --until.
    """
    out: list[date] = []
    last_year = until.year if until else date.today().year
    for year in range(since.year, last_year + 1):
        for iso in valid_periods_for_year(year):
            p = date.fromisoformat(iso)
            if p >= since and (until is None or p <= until):
                out.append(p)
    return out


def previous_period(p: date) -> date:
    """Vorherige gültige FIDE-Periode (Quartale/Zweimonatsrhythmus vor 2012-08)."""
    y, m = p.year, p.month
    for _ in range(24):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        if is_valid_fide_period(date(y, m, 1)):
            return date(y, m, 1)
    return date(y, m, 1)


# ── Ergebnisstruktur ──────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    id: str
    layer: int
    title: str
    fix_hint: str = ""
    severity: str = OK
    n_checked: int = 0
    n_findings: int = 0
    note: str = ""
    sample: list[dict] = field(default_factory=list)
    by_year: Counter = field(default_factory=Counter)
    by_federation: Counter = field(default_factory=Counter)
    worst_periods: list[tuple[str, float]] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return round(100.0 * self.n_findings / self.n_checked, 2) if self.n_checked else 0.0

    def raise_to(self, severity: str) -> None:
        if _SEVERITY_RANK[severity] > _SEVERITY_RANK[self.severity]:
            self.severity = severity


@dataclass
class AuditResult:
    since: date
    until: date | None
    periods: list[date]
    federations: list[str] | None
    checks: list[CheckResult]
    headline: dict
    by_year: dict[int, dict]
    by_federation: dict[str, dict]
    skipped_periods: list[date]

    def has_hard(self) -> bool:
        return any(c.severity == HARD for c in self.checks)

    def check(self, check_id: str) -> CheckResult | None:
        return next((c for c in self.checks if c.id == check_id), None)


# ── SQL ───────────────────────────────────────────────────────────────────────

def _fed_clause(federations: list[str] | None, alias: str = "p") -> str:
    return f"AND {alias}.federation = ANY(%(feds)s)" if federations else ""


_REF_SQL = """
    SELECT period,
           COUNT(*) FILTER (WHERE published_rating > 0)                      AS listed,
           COUNT(*) FILTER (WHERE published_rating > 0 AND num_games IS NULL) AS ng_null
    FROM rating_history
    WHERE period = ANY(%(periods)s::date[])
    GROUP BY period
"""

# Liste P mit Liste P+1 verwechselt? Unter den Spielern, die in P+1 laut Liste
# gespielt haben, dürfen Rating UND Partienzahl in P fast nie gleich sein
# (normal 1–5 %). Befund 2026-09-16: die als 2024-06 importierte Liste war
# faktisch die Juli-Liste — 31.000 von 33.000 identisch.
_REF_DUP_SQL = """
    SELECT m.p, COUNT(*) AS played_next,
           COUNT(*) FILTER (WHERE cur.published_rating = nxt.published_rating
                              AND cur.num_games = nxt.num_games) AS identical
    FROM unnest(%(ps)s::date[], %(ns)s::date[]) AS m(p, n)
    JOIN rating_history nxt ON nxt.period = m.n AND nxt.num_games > 0
    JOIN rating_history cur ON cur.fide_id = nxt.fide_id AND cur.period = m.p
    GROUP BY m.p
"""
REF_DUP_MAX_PCT = 20.0

# Eine Zeile pro Soll-Kombination, drei Kategorie-Spalten:
#   cat1  Vollständigkeit   ok | missing | listed_no_data | listed_error
#   cat2  Partienzahl       equal | fewer | fewer_first_rating | more  (nur ok)
#   cat3  Kette             ok | no_prev | ro_shift | k40 | unexplained  (nur equal)
# Zurück kommen Zähler je (Föderation, Kategorien) und eine Zufallsstichprobe
# der nicht-grünen Zeilen.
_PERIOD_SQL = """
WITH soll AS (
    SELECT rh.fide_id, rh.num_games, rh.published_rating AS pub, rh.std_rating AS ro,
           COALESCE(p.federation, '???') AS federation
    FROM rating_history rh
    LEFT JOIN players p ON p.fide_id = rh.fide_id
    WHERE rh.period = %(p)s AND rh.num_games > 0 {fed}
),
g AS (
    SELECT gr.fide_id, COUNT(*) AS n, SUM(gr.rating_change_weighted) AS s
    FROM game_results gr
    WHERE gr.period = %(p)s
      AND gr.fide_id IN (SELECT fide_id FROM soll)
    GROUP BY gr.fide_id
),
base AS (
    SELECT s.fide_id, s.federation, s.num_games, s.pub, s.ro,
           COALESCE(g.n, 0)  AS n,
           COALESCE(g.s, 0)  AS s,
           sp.status, sp.k_factor,
           prev.published_rating AS pub_prev,
           rc.amount AS corr_stored, rc.approx AS corr_approx
    FROM soll s
    LEFT JOIN g ON g.fide_id = s.fide_id
    LEFT JOIN scrape_periods sp
           ON sp.fide_id = s.fide_id AND sp.period = %(p)s
    LEFT JOIN rating_history prev
           ON prev.fide_id = s.fide_id AND prev.period = %(prev)s
          AND prev.published_rating > 0
    LEFT JOIN (
        SELECT fide_id, SUM(amount) AS amount,
               BOOL_OR(corr_type = 'fide_one_off' AND source = 'formula') AS approx
        FROM rating_corrections
        WHERE period = %(p)s AND fide_id IN (SELECT fide_id FROM soll)
        GROUP BY fide_id
    ) rc ON rc.fide_id = s.fide_id
),
base2 AS (
    -- März-2024-Korrektur: Zeilen mit source='formula' sind eine Näherung auf
    -- Basis von Ro (exakt nur für Spieler ohne Partien). Für gescrapte Spieler
    -- rechnet FIDE mit dem Rating NACH den Partien — mit dieser Formel
    -- schließen 96 Prozent der März-Fenster, mit dem gespeicherten Betrag nur 12 Prozent.
    SELECT b.*,
           CASE WHEN b.corr_stored IS NULL THEN 0
                WHEN b.corr_approx AND b.pub_prev IS NOT NULL
                THEN GREATEST(0, ROUND(0.4 * (2000 - (b.pub_prev + b.s))))
                ELSE b.corr_stored END AS corr
    FROM base b
),
c AS (
    SELECT b.*,
           b.pub - b.pub_prev - b.s - b.corr AS residual,
           CASE WHEN b.status IS NULL      THEN 'missing'
                WHEN b.status = 'ok'       THEN 'ok'
                -- Erstbewertung: die Berechnungsseite bleibt dann oft leer
                WHEN b.pub_prev IS NULL    THEN 'listed_first_rating'
                WHEN b.status = 'no_data'  THEN 'listed_no_data'
                ELSE 'listed_error' END AS cat1,
           CASE WHEN b.status IS DISTINCT FROM 'ok' THEN NULL
                WHEN b.n = b.num_games             THEN 'equal'
                WHEN b.n < b.num_games AND b.pub_prev IS NULL THEN 'fewer_first_rating'
                WHEN b.n < b.num_games             THEN 'fewer'
                ELSE 'more' END AS cat2
    FROM base2 b
),
k AS (
    SELECT c.*,
           CASE WHEN c.cat2 IS DISTINCT FROM 'equal' THEN NULL
                WHEN c.pub_prev IS NULL OR c.pub IS NULL THEN 'no_prev'
                WHEN ABS(c.residual) <= %(tol)s THEN 'ok'
                WHEN c.ro IS NOT NULL AND c.ro <> c.pub_prev THEN 'ro_shift'
                WHEN c.k_factor = 40 THEN 'k40'
                ELSE 'unexplained' END AS cat3,
           CASE WHEN c.ro IS NULL OR c.pub_prev IS NULL OR c.status IS DISTINCT FROM 'ok'
                THEN NULL ELSE c.ro = c.pub_prev END AS ro_eq,
           (c.pub_prev IS NOT NULL AND ABS(c.pub - c.pub_prev) > %(jump)s) AS jump
    FROM c
)
SELECT 'n' AS kind, federation, cat1, cat2, cat3, ro_eq, jump,
       COUNT(*) AS cnt,
       SUM(num_games)::bigint AS listed_games,
       SUM(LEAST(n, num_games))::bigint AS found_games,
       NULL::int AS fide_id, NULL::text AS detail
FROM k
GROUP BY federation, cat1, cat2, cat3, ro_eq, jump
UNION ALL
SELECT 's', federation, cat1, cat2, cat3, ro_eq, jump, 1, NULL, NULL, fide_id,
       json_build_object(
           'status', status, 'num_games', num_games, 'games', n,
           'pub_prev', pub_prev, 'pub', pub, 'ro', ro, 'k', k_factor,
           'sum_change', s, 'correction', corr, 'residual', residual
       )::text
FROM (
    SELECT k.*, ROW_NUMBER() OVER (
        PARTITION BY cat1, cat2, cat3, jump ORDER BY random()) AS rn
    FROM k
    WHERE cat1 <> 'ok' OR cat2 <> 'equal' OR cat3 NOT IN ('ok', 'no_prev') OR jump
) x
WHERE rn <= %(sample)s
"""

# Partie-Ebene: Wertebereiche, K×Δ, Elo-Formel (nur Einzelturnier-Perioden),
# Partien ohne Listeneintrag.
_GAMES_SQL = """
WITH g AS (
    SELECT gr.fide_id, gr.game_index, gr.result, gr.rating_change AS rc,
           gr.rating_change_weighted AS rcw, gr.opponent_rating AS opp,
           gr.opponent_name, gr.tournament_name,
           fn_fide_diff_cap(COALESCE(gr.tournament_start_date, gr.period)) AS cap
    FROM game_results gr
    {fed_join}
    WHERE gr.period = %(p)s {fed}
),
single AS (
    SELECT fide_id FROM g GROUP BY fide_id HAVING COUNT(DISTINCT tournament_name) <= 1
),
x AS (
    SELECT g.*, rh.std_rating AS ro, rh.num_games, sp.k_factor AS k,
           COALESCE(rh.published_rating, 0) = 0 AND pv.fide_id IS NULL AS unrated,
           (s.fide_id IS NOT NULL) AS is_single,
           COALESCE(p2.federation, '???') AS federation
    FROM g
    LEFT JOIN single s ON s.fide_id = g.fide_id
    LEFT JOIN rating_history rh ON rh.fide_id = g.fide_id AND rh.period = %(p)s
    LEFT JOIN scrape_periods sp ON sp.fide_id = g.fide_id AND sp.period = %(p)s
    LEFT JOIN rating_history pv ON pv.fide_id = g.fide_id AND pv.period = %(prev)s
          AND pv.published_rating > 0
    LEFT JOIN players p2 ON p2.fide_id = g.fide_id
),
y AS (
    SELECT x.*,
           CASE WHEN x.result IS NULL OR x.result NOT IN ('0', '0.5', '1') THEN 'bad_result'
                WHEN x.k IS NOT NULL AND x.k NOT BETWEEN 1 AND 40         THEN 'bad_k'
                WHEN x.opp IS NOT NULL AND (x.opp <= 0 OR x.opp > 2900)   THEN 'bad_opp_rating'
                WHEN x.ro IS NOT NULL AND (x.ro <= 0 OR x.ro > 2900)      THEN 'bad_ro'
                END AS range_cat,
           -- K kann je Turnier abweichen (Alters-/Ratinggrenze, 700er-Regel),
           -- scrape_periods kennt nur eines. Geprüft wird deshalb, ob K×Δ / Δ
           -- ein zulässiges ganzzahliges K ergibt — beides hat zwei Nachkommastellen,
           -- K×Δ ist also exakt.
           CASE WHEN x.rc IS NULL OR x.rcw IS NULL THEN NULL
                WHEN x.rc = 0 THEN CASE WHEN x.rcw = 0 THEN 'ok' ELSE 'mismatch' END
                WHEN ROUND(x.rcw / x.rc) NOT BETWEEN 1 AND 40
                  OR ABS(x.rcw - x.rc * ROUND(x.rcw / x.rc)) > 0.005 THEN 'mismatch'
                ELSE 'ok' END AS k_cat,
           CASE WHEN NOT x.is_single OR x.ro IS NULL OR x.opp IS NULL OR x.rc IS NULL
                     OR x.result IS NULL OR x.result NOT IN ('0', '0.5', '1') THEN NULL
                WHEN ABS((x.result::numeric - x.rc) - fn_fide_expected(
                         CASE WHEN x.cap IS NULL THEN x.ro - x.opp
                              ELSE GREATEST(-x.cap, LEAST(x.cap, x.ro - x.opp)) END
                     )) > 0.005 THEN 'mismatch'
                ELSE 'ok' END AS f_cat,
           -- Partien noch unbewerteter Spieler (weder in dieser noch in der
           -- Vorliste) sammeln sich für die Erstbewertung — erwartbar.
           CASE WHEN COALESCE(x.num_games, 0) > 0 THEN NULL
                WHEN x.unrated THEN 'unrated'
                ELSE 'unlisted' END AS unlisted
    FROM x
)
SELECT 'n' AS kind, federation, range_cat, k_cat, f_cat, unlisted,
       COUNT(*) AS cnt, COUNT(DISTINCT fide_id) AS players,
       NULL::int AS fide_id, NULL::text AS detail
FROM y
GROUP BY federation, range_cat, k_cat, f_cat, unlisted
UNION ALL
SELECT 's', federation, range_cat, k_cat, f_cat, unlisted, 1, 1, fide_id,
       json_build_object(
           'game_index', game_index, 'result', result, 'rating_change', rc,
           'weighted', rcw, 'k', k, 'ro', ro, 'opponent_rating', opp,
           'opponent', opponent_name, 'tournament', tournament_name,
           'expected', CASE WHEN ro IS NULL OR opp IS NULL THEN NULL ELSE fn_fide_expected(
                CASE WHEN cap IS NULL THEN ro - opp
                     ELSE GREATEST(-cap, LEAST(cap, ro - opp)) END) END
       )::text
FROM (
    SELECT y.*, ROW_NUMBER() OVER (
        PARTITION BY range_cat, k_cat, f_cat ORDER BY random()) AS rn
    FROM y
    WHERE range_cat IS NOT NULL OR k_cat = 'mismatch' OR f_cat = 'mismatch'
) z
WHERE rn <= %(sample)s
"""

_NEVER_SCRAPED_SQL = """
WITH soll AS (
    SELECT rh.fide_id, COUNT(*) AS listed_periods, SUM(rh.num_games)::bigint AS listed_games
    FROM rating_history rh
    WHERE rh.period = ANY(%(periods)s::date[]) AND rh.num_games > 0
    GROUP BY rh.fide_id
),
t AS (
    SELECT s.*, COALESCE(p.federation, '???') AS federation, p.name, p.active,
           NOT EXISTS (
               SELECT 1 FROM scrape_periods sp
               WHERE sp.fide_id = s.fide_id AND sp.period = ANY(%(periods)s::date[])
           ) AS never
    FROM soll s
    LEFT JOIN players p ON p.fide_id = s.fide_id
    WHERE TRUE {fed}
)
SELECT 'n', federation, active, never, COUNT(*), SUM(listed_games)::bigint, NULL::int, NULL::text
FROM t GROUP BY federation, active, never
UNION ALL
SELECT 's', federation, active, never, 1, listed_games, fide_id,
       json_build_object('name', name, 'listed_periods', listed_periods,
                         'listed_games', listed_games)::text
FROM (
    SELECT t.*, ROW_NUMBER() OVER (ORDER BY listed_games DESC) AS rn
    FROM t WHERE never
) z
WHERE rn <= %(sample)s
"""


# ── Ausführung ────────────────────────────────────────────────────────────────

ConnFactory = Callable[[], object]


def _open(conn_factory: ConnFactory):
    conn = conn_factory()
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
    return conn


def _query_period(conn_factory: ConnFactory, p: date, params: dict,
                  layers: set[int]) -> tuple[date, list, list]:
    conn = _open(conn_factory)
    try:
        fed = _fed_clause(params.get("feds"))
        per_rows: list = []
        game_rows: list = []
        with conn.cursor() as cur:
            args = {**params, "p": p, "prev": previous_period(p)}
            if layers & {1, 2, 3}:
                cur.execute(_PERIOD_SQL.format(fed=fed), args)
                per_rows = cur.fetchall()
            if layers & {2, 3}:
                fed_join = ("JOIN players p ON p.fide_id = gr.fide_id"
                            if params.get("feds") else "")
                cur.execute(_GAMES_SQL.format(fed=fed, fed_join=fed_join), args)
                game_rows = cur.fetchall()
        return p, per_rows, game_rows
    finally:
        conn.close()


def _sample_add(check: CheckResult, limit: int, p: date, federation: str,
                fide_id: int, detail: str, **extra) -> None:
    if len(check.sample) >= limit:
        return
    row = {"period": p.isoformat(), "fide_id": fide_id, "federation": federation, **extra}
    row.update(json.loads(detail))
    check.sample.append(row)


def run_audit(
    conn_factory: ConnFactory,
    since: date,
    until: date | None = None,
    layers: set[int] | None = None,
    federations: list[str] | None = None,
    sample: int = 20,
    jobs: int = 2,
    chain_tolerance: float = DEFAULT_CHAIN_TOLERANCE,
    chain_threshold_pct: float = DEFAULT_CHAIN_THRESHOLD_PCT,
    formula_threshold_pct: float = DEFAULT_FORMULA_THRESHOLD_PCT,
    no_data_threshold_pct: float = DEFAULT_NO_DATA_THRESHOLD_PCT,
    fewer_threshold_pct: float = DEFAULT_FEWER_THRESHOLD_PCT,
    jump: int = DEFAULT_JUMP,
    with_integrity: bool = True,
    progress: Callable[[int, int, date], None] | None = None,
) -> AuditResult:
    """Führt die Prüfung aus. conn_factory liefert je Aufruf eine neue Verbindung
    (für die parallelen Periodenabfragen), z.B. orchestrator.setup_db.connect.
    """
    layers = set(layers or LAYERS)
    periods = audit_periods(since, until)
    params = {
        "periods": [p.isoformat() for p in periods],
        "feds": federations,
        "tol": chain_tolerance,
        "jump": jump,
        "sample": sample,
    }

    C = {c.id: c for c in [
        CheckResult("ref_lists_present", 0, "Offizielle Liste je Periode vorhanden",
                    "Liste mit scripts/monthly_update.sh YYYY-MM-01 bzw. "
                    "import_rating_snapshots.py --file … nachimportieren"),
        CheckResult("ref_list_not_shifted", 0, "Liste nicht mit Folgeliste verwechselt",
                    "Listendatei der Periode prüfen und neu importieren "
                    "(import_rating_snapshots.py --file …); danach Audit wiederholen"),
        CheckResult("ref_num_games_present", 0, "Partienzahl (num_games) in der Liste vorhanden",
                    "Ältere Listen ohne SGm-Spalte: Soll für diese Perioden unvollständig"),
        CheckResult("players_never_scraped", 1, "Spieler mit Partien laut Liste, im Zeitraum nie gescrapt",
                    "Spieler fehlen in der Orchestrator-Population — P0-Tier/"
                    "generate_new_entrant_batches.py prüfen oder backfill.py --fide-ids …"),
        CheckResult("periods_missing", 1, "Perioden mit Partien laut Liste, nicht gescrapt",
                    "Offene Arbeit: Queue-Gruppen für Föderation×Jahr requeuen"),
        CheckResult("periods_listed_no_data", 1, "Liste sagt Partien, Scrape sagt no_data/error",
                    "Stichprobe auf ratings.fide.com; bei einer Häufung in einer Periode (Muster Vorfall "
                    "2026-07-14) Kombos löschen und neu scrapen"),
        CheckResult("periods_no_data_first_rating", 1, "no_data bei Erstbewertung (keine Vorliste)",
                    "Meist erwartbar: die Berechnungsseite bleibt bei Erstbewertung oft leer; "
                    "Stichprobe auf ratings.fide.com prüfen"),
        CheckResult("game_count_fewer", 2, "Weniger Partien als in der Liste",
                    "Erst Stichprobe auf ratings.fide.com: zeigt FIDE selbst weniger, ist es eine "
                    "FIDE-Abweichung. Sonst Kombo löschen (scrape_periods + game_results) und neu scrapen"),
        CheckResult("game_count_first_rating", 2, "Weniger Partien bei Erstbewertung",
                    "Erwartbar: die Liste zählt bei Erstbewertung auch Partien früherer Perioden"),
        CheckResult("game_count_more", 2, "Mehr Partien als in der Liste",
                    "Doppelte game_index-Zeilen oder nachgewertete Turniere prüfen"),
        CheckResult("games_not_in_list", 2, "Gescrapte Partien bewerteter Spieler ohne Listeneintrag",
                    "Nachträglich gewertete Turniere oder aus der Liste gefallene Spieler; "
                    "bei Häufung Periodenzuordnung prüfen"),
        CheckResult("games_unrated_players", 2, "Partien noch unbewerteter Spieler (vor Erstbewertung)",
                    "Erwartbar: zählen erst mit der Erstbewertung in einer Liste"),
        CheckResult("chain_matches_list", 3, "Liste[P] − Liste[vorher] = Σ K×Δ (+ Korrektur)",
                    "Unerklärte Fenster mit scripts/reconcile_ratings.py / quality_check.py vertiefen"),
        CheckResult("ro_matches_list", 3, "Ro der Berechnung = Vorliste",
                    "Nur Kennzahl: Abweichung ist bei nachgewerteten Turnieren normal"),
        CheckResult("game_formula", 3, "Δ je Partie = Ergebnis − E(Ro − Gegner)",
                    "Parser/Spaltenzuordnung prüfen (scraper/parser.py), Stichprobe auf ratings.fide.com"),
        CheckResult("k_times_change", 3, "K × Δ = gewichtete Änderung",
                    "Parser prüfen: Spalten 7 (Δ), 8 (K), 9 (K×Δ)"),
        CheckResult("value_ranges", 3, "Wertebereiche (Ergebnis, K, Ratings)",
                    "Parser prüfen, betroffene Kombos neu scrapen"),
        CheckResult("rating_jumps", 3, f"Listensprung > {jump} Punkte in einer Periode",
                    "Nur zur Sichtung: Korrekturen, Erstbewertungen, Datenfehler der Liste"),
    ]}

    by_year: dict[int, Counter] = defaultdict(Counter)
    by_fed: dict[str, Counter] = defaultdict(Counter)
    skipped: list[date] = []

    # ── Ebene 0 ──
    listed: dict[date, int] = {}
    conn = _open(conn_factory)
    try:
        with conn.cursor() as cur:
            cur.execute(_REF_SQL, params)
            ref = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    finally:
        conn.close()
    for i, p in enumerate(periods):
        n_listed, n_null = ref.get(p, (0, 0))
        listed[p] = n_listed
        neighbours = [ref.get(q, (0, 0))[0] for q in periods[max(0, i - 3): i + 4] if q != p]
        neighbours = [n for n in neighbours if n > 0]
        median = statistics.median(neighbours) if neighbours else 0
        ref0, ref1 = C["ref_lists_present"], C["ref_num_games_present"]
        ref0.n_checked += 1
        ref1.n_checked += n_listed
        ref1.n_findings += n_null
        if n_listed == 0 or (median and n_listed < REF_MIN_RATIO * median):
            ref0.n_findings += 1
            ref0.by_year[p.year] += 1
            ref0.sample.append({"period": p.isoformat(), "listed": n_listed,
                                "neighbour_median": median})
            skipped.append(p)
    nexts = [date(p.year + (p.month == 12), p.month % 12 + 1, 1) for p in periods]
    conn = _open(conn_factory)
    try:
        with conn.cursor() as cur:
            cur.execute(_REF_DUP_SQL, {"ps": [p.isoformat() for p in periods],
                                       "ns": [n.isoformat() for n in nexts]})
            dup = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    finally:
        conn.close()
    shifted = C["ref_list_not_shifted"]
    for p in periods:
        n_next, n_same = dup.get(p, (0, 0))
        if not n_next:
            continue  # Folgeliste noch nicht da
        shifted.n_checked += 1
        pct = 100.0 * n_same / n_next
        shifted.worst_periods.append((p.isoformat(), round(pct, 1)))
        if pct > REF_DUP_MAX_PCT:
            shifted.n_findings += 1
            shifted.by_year[p.year] += 1
            shifted.sample.append({"period": p.isoformat(), "played_next": n_next,
                                   "identical_to_next": n_same, "pct": round(pct, 1)})
            if p not in skipped:
                skipped.append(p)
    shifted.worst_periods.sort(key=lambda t: -t[1])
    shifted.worst_periods = shifted.worst_periods[:5]
    if shifted.n_findings:
        shifted.raise_to(HARD)
        shifted.note = (f"Schwelle {REF_DUP_MAX_PCT} % identische Einträge; betroffene Perioden "
                        "werden in Ebene 1–3 übersprungen, die Kette der Folgeperiode ist unzuverlässig")
    skipped.sort()

    if C["ref_lists_present"].n_findings:
        C["ref_lists_present"].raise_to(HARD)
        C["ref_lists_present"].note = ("Ebenen 1–3 überspringen diese Perioden — "
                                       "ohne Liste gäbe es kein Soll und sie sähen vollständig aus")
    if C["ref_num_games_present"].n_findings:
        C["ref_num_games_present"].raise_to(INFO)

    run_periods = [p for p in periods if p not in skipped]

    # ── Ebenen 1–3, je Periode ──
    if layers & {1, 2, 3} and run_periods:
        # je Check: Periode → [Befunde, geprüft], für die Schwellen je Periode
        pp: dict[str, dict[date, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        per_period_ro: dict[date, tuple[int, int]] = {}
        prev_unreliable: list[date] = []
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = [pool.submit(_query_period, conn_factory, p, params, layers)
                       for p in run_periods]
            for fut in as_completed(futures):
                p, per_rows, game_rows = fut.result()
                done += 1
                if progress:
                    progress(done, len(run_periods), p)
                chain_n = chain_bad = 0
                ro_n = ro_bad = 0
                # Vorliste unbrauchbar (fehlt/verwechselt) → Kette und Ro dieser
                # Periode messen nur den Listenfehler, nicht die Daten.
                prev_bad = previous_period(p) in skipped
                if prev_bad:
                    prev_unreliable.append(p)
                for (kind, fed, cat1, cat2, cat3, ro_eq, is_jump, cnt,
                     listed_games, found_games, fide_id, detail) in per_rows:
                    if kind == "s":
                        extra = dict(p=p, federation=fed, fide_id=fide_id, detail=detail)
                        if cat1 == "missing":
                            _sample_add(C["periods_missing"], sample, **extra)
                        elif cat1 == "listed_first_rating":
                            _sample_add(C["periods_no_data_first_rating"], sample, **extra)
                        elif cat1 != "ok":
                            _sample_add(C["periods_listed_no_data"], sample, **extra)
                        elif cat2 in ("fewer", "more", "fewer_first_rating"):
                            key = {"fewer": "game_count_fewer", "more": "game_count_more",
                                   "fewer_first_rating": "game_count_first_rating"}[cat2]
                            _sample_add(C[key], sample, **extra)
                        elif cat3 in ("unexplained", "k40", "ro_shift") and not prev_bad:
                            _sample_add(C["chain_matches_list"], sample, category=cat3, **extra)
                        if is_jump:
                            _sample_add(C["rating_jumps"], sample, **extra)
                        continue

                    by_year[p.year]["soll"] += cnt
                    by_year[p.year]["listed_games"] += listed_games
                    by_year[p.year]["found_games"] += found_games
                    by_fed[fed]["soll"] += cnt
                    by_fed[fed]["listed_games"] += listed_games
                    by_fed[fed]["found_games"] += found_games
                    by_year[p.year][cat1] += cnt
                    by_fed[fed][cat1] += cnt

                    if cat1 != "missing":
                        pp["periods_listed_no_data"][p][1] += cnt
                        if cat1 in ("listed_no_data", "listed_error"):
                            pp["periods_listed_no_data"][p][0] += cnt
                    if cat2 is not None:
                        pp["game_count_fewer"][p][1] += cnt
                        if cat2 == "fewer":
                            pp["game_count_fewer"][p][0] += cnt

                    for cid, hit in (("periods_missing", cat1 == "missing"),
                                     ("periods_listed_no_data", cat1 in ("listed_no_data", "listed_error")),
                                     ("periods_no_data_first_rating", cat1 == "listed_first_rating")):
                        C[cid].n_checked += cnt
                        if hit:
                            C[cid].n_findings += cnt
                            C[cid].by_year[p.year] += cnt
                            C[cid].by_federation[fed] += cnt

                    if cat2 is not None:
                        for cid, c2 in (("game_count_fewer", "fewer"),
                                        ("game_count_first_rating", "fewer_first_rating"),
                                        ("game_count_more", "more")):
                            C[cid].n_checked += cnt
                            if cat2 == c2:
                                C[cid].n_findings += cnt
                                C[cid].by_year[p.year] += cnt
                                C[cid].by_federation[fed] += cnt
                        if cat2 == "equal":
                            by_year[p.year]["complete"] += cnt
                            by_fed[fed]["complete"] += cnt

                    if cat3 is not None and cat3 != "no_prev" and not prev_bad:
                        ch = C["chain_matches_list"]
                        ch.n_checked += cnt
                        chain_n += cnt
                        by_year[p.year]["chain_checked"] += cnt
                        if cat3 != "ok":
                            ch.n_findings += cnt
                            ch.by_federation[f"{fed}"] += cnt
                            by_year[p.year][f"chain_{cat3}"] += cnt
                        if cat3 == "unexplained":
                            chain_bad += cnt
                            ch.by_year[p.year] += cnt
                        else:
                            by_year[p.year]["chain_ok" if cat3 == "ok" else "chain_explained"] += cnt

                    if ro_eq is not None and not prev_bad:
                        C["ro_matches_list"].n_checked += cnt
                        ro_n += cnt
                        if not ro_eq:
                            ro_bad += cnt
                            C["ro_matches_list"].n_findings += cnt
                            C["ro_matches_list"].by_year[p.year] += cnt
                    C["rating_jumps"].n_checked += cnt
                    if is_jump:
                        C["rating_jumps"].n_findings += cnt
                        C["rating_jumps"].by_year[p.year] += cnt
                if chain_n:
                    pp["chain_matches_list"][p] = [chain_bad, chain_n]
                if ro_n:
                    per_period_ro[p] = (ro_bad, ro_n)

                f_n = f_bad = 0
                for (kind, fed, range_cat, k_cat, f_cat, unlisted, cnt, players,
                     fide_id, detail) in game_rows:
                    if kind == "s":
                        extra = dict(p=p, federation=fed, fide_id=fide_id, detail=detail)
                        if range_cat:
                            _sample_add(C["value_ranges"], sample, category=range_cat, **extra)
                        if k_cat == "mismatch":
                            _sample_add(C["k_times_change"], sample, **extra)
                        if f_cat == "mismatch":
                            _sample_add(C["game_formula"], sample, **extra)
                        continue
                    by_year[p.year]["games"] += cnt
                    vr = C["value_ranges"]
                    vr.n_checked += cnt
                    if range_cat:
                        vr.n_findings += cnt
                        vr.by_year[p.year] += cnt
                        vr.by_federation[range_cat] += cnt
                    if k_cat is not None:
                        C["k_times_change"].n_checked += cnt
                        if k_cat == "mismatch":
                            C["k_times_change"].n_findings += cnt
                            C["k_times_change"].by_year[p.year] += cnt
                    if f_cat is not None:
                        C["game_formula"].n_checked += cnt
                        f_n += cnt
                        if f_cat == "mismatch":
                            f_bad += cnt
                            C["game_formula"].n_findings += cnt
                            C["game_formula"].by_year[p.year] += cnt
                            C["game_formula"].by_federation[fed] += cnt
                    for cid, hit in (("games_not_in_list", unlisted == "unlisted"),
                                     ("games_unrated_players", unlisted == "unrated")):
                        C[cid].n_checked += cnt
                        if hit:
                            C[cid].n_findings += cnt
                            C[cid].by_year[p.year] += cnt
                            C[cid].by_federation[fed] += cnt
                if f_n:
                    pp["game_formula"][p] = [f_bad, f_n]

        # Schweregrade Ebene 1–3
        if C["periods_missing"].n_findings:
            C["periods_missing"].raise_to(HARD)
        for cid in ("game_count_more", "games_not_in_list"):
            if C[cid].n_findings:
                C[cid].raise_to(SOFT)
        ro = C["ro_matches_list"]
        ro.worst_periods = sorted(
            ((p.isoformat(), round(100.0 * b / n, 1)) for p, (b, n) in per_period_ro.items()),
            key=lambda t: -t[1])[:5]
        ro.note = ("Abweichung ist bei nachgewerteten Turnieren normal (~30 %); "
                   "springt eine Periode gegen 100 %, ist die Vorliste verdächtig")
        for cid in ("game_count_first_rating", "ro_matches_list", "rating_jumps",
                    "periods_no_data_first_rating", "games_unrated_players"):
            if C[cid].n_findings:
                C[cid].raise_to(INFO)
        for cid in ("k_times_change", "value_ranges"):
            if C[cid].n_findings:
                C[cid].raise_to(HARD)

        _apply_period_threshold(
            C["periods_listed_no_data"], pp["periods_listed_no_data"],
            no_data_threshold_pct, "der versuchten Kombos")
        _apply_period_threshold(
            C["game_count_fewer"], pp["game_count_fewer"],
            fewer_threshold_pct, "der gescrapten Kombos")
        ch = C["chain_matches_list"]
        n_unexplained = sum(b for b, _ in pp["chain_matches_list"].values())
        _apply_period_threshold(
            ch, pp["chain_matches_list"], chain_threshold_pct, "unerklärt",
            prefix=f"{n_unexplained:,} unerklärt, Rest erklärt (K=40 / Ro-Verschiebung). "
                   + (f"Ohne verlässliche Vorliste ausgelassen: "
                      f"{', '.join(f'{p:%Y-%m}' for p in sorted(prev_unreliable))}. "
                      if prev_unreliable else ""))
        _apply_period_threshold(
            C["game_formula"], pp["game_formula"], formula_threshold_pct, "der Partien",
            prefix="Nur Perioden mit genau einem Turnier (Summary-Zeile liefert ein Ro). ",
            digits=3)

    # ── Ebene 1: nie gescrapte Spieler (über den ganzen Zeitraum) ──
    if 1 in layers and run_periods:
        conn = _open(conn_factory)
        try:
            with conn.cursor() as cur:
                cur.execute(_NEVER_SCRAPED_SQL.format(fed=_fed_clause(federations)),
                            {**params, "periods": [p.isoformat() for p in run_periods]})
                rows = cur.fetchall()
        finally:
            conn.close()
        ns = C["players_never_scraped"]
        inactive_never = 0
        for kind, fed, active, never, cnt, games, fide_id, detail in rows:
            if kind == "s":
                row = {"fide_id": fide_id, "federation": fed, "active": active}
                row.update(json.loads(detail))
                ns.sample.append(row)
                continue
            ns.n_checked += cnt
            by_fed[fed]["players"] += cnt
            if never:
                ns.n_findings += cnt
                ns.by_federation[fed] += cnt
                by_fed[fed]["players_never"] += cnt
                if not active:
                    inactive_never += cnt
        ns.note = f"davon heute inaktiv: {inactive_never:,}"
        if ns.n_findings:
            ns.raise_to(HARD)

    checks = [c for c in C.values() if c.layer in layers]

    # ── Ebene 2: strukturelle Checks aus integrity.py, zeitlich gefiltert ──
    if 2 in layers and with_integrity:
        conn = _open(conn_factory)
        try:
            for fn, cid, title in (
                (check_ok_without_games, "ok_without_games", "status=ok ohne Partien"),
                (check_no_data_with_games, "no_data_with_games", "status=no_data mit Partien"),
                (check_blocked_error_rows, "blocked_error_rows", "Fehlerzeilen blockieren Retry"),
                (check_orphan_games, "orphan_games", "Partien ohne scrape_periods-Zeile"),
            ):
                findings = fn(conn, since=since, until=until)
                cr = CheckResult(f"integrity:{cid}", 2, title,
                                 "siehe scripts/verify_scrape_integrity.py --check " + cid)
                cr.n_findings = len(findings)
                cr.note = "ohne Föderationsfilter" if federations else ""
                for f in findings:
                    cr.raise_to(f["severity"])
                    cr.by_year[f["period"].year] += 1
                cr.sample = [
                    {k: (v.isoformat() if isinstance(v, date) else v)
                     for k, v in f.items() if k not in ("check", "subject")}
                    for f in findings[:sample]
                ]
                checks.append(cr)
        finally:
            conn.close()

    # ── Kopfzahlen ──
    soll = sum(v["soll"] for v in by_year.values())
    listed_games = sum(v["listed_games"] for v in by_year.values())
    found_games = sum(v["found_games"] for v in by_year.values())
    chain_checked = sum(v["chain_checked"] for v in by_year.values())
    headline = {
        "periods": len(periods),
        "periods_skipped": len(skipped),
        "soll_combos": soll,
        "pct_periods_scraped": _pct(sum(v["ok"] for v in by_year.values()), soll),
        "pct_periods_complete": _pct(sum(v["complete"] for v in by_year.values()), soll),
        "pct_games_found": _pct(found_games, listed_games),
        "listed_games": listed_games,
        "found_games": found_games,
        "chain_checked": chain_checked,
        "pct_chain_ok": _pct(sum(v["chain_ok"] for v in by_year.values()), chain_checked),
        "pct_chain_unexplained": _pct(sum(v["chain_unexplained"] for v in by_year.values()),
                                      chain_checked),
        "hard_checks": sum(1 for c in checks if c.severity == HARD),
        "soft_checks": sum(1 for c in checks if c.severity == SOFT),
    }
    if C["players_never_scraped"] in checks:
        ns = C["players_never_scraped"]
        headline["soll_players"] = ns.n_checked
        headline["pct_players_touched"] = _pct(ns.n_checked - ns.n_findings, ns.n_checked)

    return AuditResult(
        since=since, until=until, periods=periods, federations=federations,
        checks=checks, headline=headline,
        by_year={y: dict(v) for y, v in sorted(by_year.items())},
        by_federation={f: dict(v) for f, v in by_fed.items()},
        skipped_periods=skipped,
    )


def _apply_period_threshold(check: CheckResult, per_period: dict[date, list[int]],
                            threshold_pct: float, what: str, prefix: str = "",
                            digits: int = 2) -> None:
    """Hart, sobald eine Periode die Schwelle reißt; sonst weich bei Befunden."""
    rates = sorted(((p, 100.0 * b / n) for p, (b, n) in per_period.items() if n),
                   key=lambda t: -t[1])
    check.worst_periods = [(p.isoformat(), round(v, digits)) for p, v in rates[:10] if v > 0]
    over = [p for p, v in rates if v > threshold_pct]
    check.note = (f"{prefix}Schwelle {threshold_pct} % {what} je Periode, "
                  f"überschritten in {len(over)} Perioden"
                  + (f": {', '.join(f'{p:%Y-%m}' for p in sorted(over)[:12])}" if over else ""))
    if over:
        check.raise_to(HARD)
    elif check.n_findings:
        check.raise_to(SOFT)


def _pct(a: int | float, b: int | float) -> float:
    return round(100.0 * float(a) / float(b), 2) if b else 0.0
