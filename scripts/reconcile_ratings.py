#!/usr/bin/env python3
"""Reconciliation check: official monthly rating lists vs. scraped game data.

For every player the official published ratings (rating_history.published_rating,
imported from the monthly FIDE TXT lists) are compared against the sum of scraped
per-game rating changes (game_results.rating_change_weighted):

    expected  = published[T2] - published[T1]        (consecutive snapshots)
    scraped   = SUM(rating_change_weighted)  for T1 < period <= T2
    residual  = expected - scraped - known_corrections

A window whose residual is not explained by any rule indicates broken or
incomplete scraping.

Rule layer (extensible — add a named Rule class, the core stays untouched):
    KnownCorrectionRule   adjusts residuals by rating_corrections amounts
                          (e.g. the FIDE March-2024 one-off boost)
    ToleranceRule         |residual| <= 1 counts as rounding noise, explained
    RollingWindowRule     a tournament's effect may slip into the next official
                          list ("Monatsverschiebung"); consecutive windows whose
                          cumulative residual cancels out within a configurable
                          span (default 2 months, then 12) are explained together

Pre-2012-08 quarterly lists need no special casing: windows are built from
consecutive snapshots, so a quarterly window simply spans three game months.

Name matching between the official lists (players.name) and scraped game data
(game_results.opponent_name) is fuzzy (rapidfuzz) and never discards silently:
every name lands in a confidence bucket (exact / high / uncertain / unmatched)
and the uncertain ones are listed explicitly.

Usage:
    python reconcile_ratings.py run [--group female_top,male_control]
                                    [--tolerance 1] [--windows 2,12]
                                    [--csv out.csv]
    python reconcile_ratings.py verify-names [--sample 500] [--group ...]
"""

import argparse
import csv
import logging
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.db import is_valid_fide_period

logger = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 1.0
DEFAULT_WINDOWS = (2, 12)


# ── Month arithmetic ──────────────────────────────────────────────────────────

def months_between(a: date, b: date) -> int:
    """Whole months from a to b (b >= a)."""
    return (b.year - a.year) * 12 + (b.month - a.month)


# ── Core data structures ──────────────────────────────────────────────────────

@dataclass
class Window:
    """One reconciliation window between two consecutive official snapshots."""
    fide_id: int
    period_start: date          # snapshot T1 (exclusive for games)
    period_end: date            # snapshot T2 (inclusive for games)
    published_start: int
    published_end: int
    scraped_change: float       # SUM(rating_change_weighted) in (T1, T2]
    missing_periods: int = 0    # months in window without scrape_periods entry

    # filled by the engine:
    adjustment: float = 0.0     # sum of Rule.adjust() amounts
    status: str = "unexplained"
    explained_by: str | None = None

    @property
    def expected_change(self) -> int:
        return self.published_end - self.published_start

    @property
    def residual(self) -> float:
        return self.expected_change - self.scraped_change - self.adjustment


@dataclass
class PlayerSeries:
    """All windows of one player, ordered by period."""
    fide_id: int
    windows: list[Window] = field(default_factory=list)


# ── Rule layer ────────────────────────────────────────────────────────────────

class Rule:
    """Named exception rule.

    adjust()  shifts a window's residual (known non-game rating changes);
    explain() marks windows as explained. Either hook may be a no-op.
    New special cases are added as new Rule subclasses appended to the rule
    list — the reconciliation core never changes.
    """
    name: str = "rule"

    def adjust(self, window: Window) -> float:
        return 0.0

    def explain(self, series: PlayerSeries) -> None:
        return None


class KnownCorrectionRule(Rule):
    """Applies known non-game corrections (rating_corrections table).

    corrections: {(fide_id, period): amount}. A correction at period T is
    embedded in published_rating[T], so it belongs to the window with
    period_start < T <= period_end.
    """
    name = "known_correction"

    def __init__(self, corrections: dict[tuple[int, date], float]):
        self._by_player: dict[int, list[tuple[date, float]]] = {}
        for (fide_id, period), amount in corrections.items():
            self._by_player.setdefault(fide_id, []).append((period, amount))

    def adjust(self, window: Window) -> float:
        total = 0.0
        for period, amount in self._by_player.get(window.fide_id, []):
            if window.period_start < period <= window.period_end:
                total += amount
        return total


class NoGameDataRule(Rule):
    """Windows without a single valid FIDE game period are unverifiable.

    FIDE provides individual game data only from 2008-04 on (quarterly at
    first). rating_history reaches further back, so early windows have an
    official delta but structurally nothing to scrape — that is missing FIDE
    data, not a scraping error.
    """
    name = "no_game_data"

    def explain(self, series: PlayerSeries) -> None:
        for w in series.windows:
            if w.status != "unexplained":
                continue
            m = date(w.period_start.year, w.period_start.month, 1)
            has_game_period = False
            while m < w.period_end:
                m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
                if is_valid_fide_period(m):
                    has_game_period = True
                    break
            if not has_game_period:
                w.status = "explained"
                w.explained_by = self.name


class ToleranceRule(Rule):
    """|residual| <= tolerance is rounding noise, not an error."""
    name = "tolerance"

    def __init__(self, tolerance: float = DEFAULT_TOLERANCE):
        self.tolerance = tolerance

    def explain(self, series: PlayerSeries) -> None:
        for w in series.windows:
            if w.status == "unexplained" and abs(w.residual) <= self.tolerance:
                w.status = "explained"
                w.explained_by = self.name


class RollingWindowRule(Rule):
    """Month-shift effect: a tournament's rating effect can land in the next
    official list. Consecutive unexplained windows whose cumulative residual
    cancels out (within tolerance) over a span of <= max_months are explained
    together as one shifted block.
    """

    def __init__(self, max_months: int, tolerance: float = DEFAULT_TOLERANCE):
        self.max_months = max_months
        self.tolerance = tolerance
        self.name = f"rolling_{max_months}m"

    def explain(self, series: PlayerSeries) -> None:
        wins = series.windows
        i = 0
        while i < len(wins):
            if wins[i].status != "unexplained":
                i += 1
                continue
            # Accumulate residuals of ALL windows in the span (explained quiet
            # months in between contribute their near-zero residuals and must
            # not block the bridge). The cancelling partner window itself has
            # to be unexplained — otherwise we would just re-explain noise.
            cum = 0.0
            for j in range(i, len(wins)):
                span = months_between(wins[i].period_start, wins[j].period_end)
                if span > self.max_months:
                    break
                cum += wins[j].residual
                if (j > i and wins[j].status == "unexplained"
                        and abs(cum) <= self.tolerance):
                    for w in wins[i:j + 1]:
                        if w.status == "unexplained":
                            w.status = "explained"
                            w.explained_by = self.name
                    i = j
                    break
            i += 1


def default_rules(
    corrections: dict[tuple[int, date], float] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    rolling_windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> list[Rule]:
    rules: list[Rule] = [KnownCorrectionRule(corrections or {})]
    rules.append(NoGameDataRule())
    rules.append(ToleranceRule(tolerance))
    for m in rolling_windows:
        rules.append(RollingWindowRule(m, tolerance))
    return rules


# ── Reconciliation engine ─────────────────────────────────────────────────────

def reconcile(series: PlayerSeries, rules: list[Rule]) -> PlayerSeries:
    """Run the rule chain over one player's windows (in place)."""
    for w in series.windows:
        w.adjustment = sum(rule.adjust(w) for rule in rules)
    for rule in rules:
        rule.explain(series)
    return series


def build_series(
    fide_id: int,
    snapshots: list[tuple[date, int]],
    monthly_scraped: dict[date, float],
    scraped_months: set[date] | None = None,
) -> PlayerSeries:
    """Build windows from consecutive official snapshots.

    snapshots:       [(period, published_rating)] sorted by period
    monthly_scraped: {game period: SUM(rating_change_weighted)}
    scraped_months:  periods with a scrape_periods entry (for the
                     missing_periods diagnostic); None disables the check
    """
    series = PlayerSeries(fide_id=fide_id)
    snaps = sorted(snapshots)
    for (t1, r1), (t2, r2) in zip(snaps, snaps[1:]):
        scraped = sum(v for p, v in monthly_scraped.items() if t1 < p <= t2)
        missing = 0
        if scraped_months is not None:
            m = date(t1.year, t1.month, 1)
            while m < t2:
                m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
                if is_valid_fide_period(m) and m not in scraped_months:
                    missing += 1
        series.windows.append(Window(
            fide_id=fide_id, period_start=t1, period_end=t2,
            published_start=r1, published_end=r2,
            scraped_change=scraped, missing_periods=missing,
        ))
    return series


# ── Fuzzy name matching ───────────────────────────────────────────────────────

BUCKET_EXACT = "exact"
BUCKET_HIGH = "high"
BUCKET_UNCERTAIN = "uncertain"
BUCKET_UNMATCHED = "unmatched"


def normalize_name(name: str) -> str:
    """Case-fold, strip diacritics, sort tokens ('Mueller, Hans' == 'hans mueller')."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    tokens = s.lower().replace(",", " ").replace(".", " ").split()
    return " ".join(sorted(tokens))


@dataclass
class MatchResult:
    query: str
    matched: str | None
    score: float
    bucket: str


class NameMatcher:
    """Fuzzy matcher between scraped names and the official rating list.

    Every query lands in a bucket — uncertain matches are surfaced for review
    instead of being silently dropped.
    """

    def __init__(self, official_names: list[str],
                 high_threshold: float = 90.0,
                 uncertain_threshold: float = 75.0):
        from rapidfuzz import process, fuzz  # lazy: keep core importable
        self._process, self._fuzz = process, fuzz
        self.high_threshold = high_threshold
        self.uncertain_threshold = uncertain_threshold
        self._originals = official_names
        self._normalized = [normalize_name(n) for n in official_names]

    def match(self, query: str) -> MatchResult:
        q = normalize_name(query)
        if not q or not self._normalized:
            return MatchResult(query, None, 0.0, BUCKET_UNMATCHED)
        best = self._process.extractOne(q, self._normalized,
                                        scorer=self._fuzz.token_sort_ratio)
        norm, score, idx = best
        matched = self._originals[idx]
        if q == norm:
            bucket = BUCKET_EXACT
        elif score >= self.high_threshold:
            bucket = BUCKET_HIGH
        elif score >= self.uncertain_threshold:
            bucket = BUCKET_UNCERTAIN
        else:
            bucket = BUCKET_UNMATCHED
            matched = None
        return MatchResult(query, matched, float(score), bucket)

    def match_all(self, queries: list[str]) -> list[MatchResult]:
        return [self.match(q) for q in queries]


# ── PostgreSQL loaders ────────────────────────────────────────────────────────

_LOAD_SNAPSHOTS_SQL = """
    SELECT rh.fide_id, rh.period, rh.published_rating
    FROM rating_history rh
    JOIN players p USING (fide_id)
    WHERE rh.published_rating IS NOT NULL
      AND rh.fide_id IN (SELECT DISTINCT fide_id FROM scrape_periods)
      {group_filter}
    ORDER BY rh.fide_id, rh.period
"""

_LOAD_SCRAPED_SQL = """
    SELECT gr.fide_id, gr.period, SUM(gr.rating_change_weighted)
    FROM game_results gr
    JOIN players p USING (fide_id)
    WHERE TRUE {group_filter}
    GROUP BY gr.fide_id, gr.period
"""

_LOAD_SCRAPED_MONTHS_SQL = """
    SELECT sp.fide_id, sp.period
    FROM scrape_periods sp
    JOIN players p USING (fide_id)
    WHERE TRUE {group_filter}
"""

_LOAD_CORRECTIONS_SQL = """
    SELECT fide_id, period, SUM(amount)
    FROM rating_corrections
    GROUP BY fide_id, period
"""


def _group_filter(groups: list[str] | None) -> tuple[str, tuple]:
    if not groups:
        return "", ()
    return "AND p.analysis_group = ANY(%s)", (groups,)


def load_player_series(conn, groups: list[str] | None = None) -> list[PlayerSeries]:
    gf, params = _group_filter(groups)

    with conn.cursor() as cur:
        cur.execute(_LOAD_SNAPSHOTS_SQL.format(group_filter=gf), params)
        snapshots: dict[int, list[tuple[date, int]]] = {}
        for fide_id, period, rating in cur.fetchall():
            snapshots.setdefault(fide_id, []).append((period, rating))

        cur.execute(_LOAD_SCRAPED_SQL.format(group_filter=gf), params)
        scraped: dict[int, dict[date, float]] = {}
        for fide_id, period, total in cur.fetchall():
            scraped.setdefault(fide_id, {})[period] = float(total or 0)

        cur.execute(_LOAD_SCRAPED_MONTHS_SQL.format(group_filter=gf), params)
        months: dict[int, set[date]] = {}
        for fide_id, period in cur.fetchall():
            months.setdefault(fide_id, set()).add(period)

    return [
        build_series(fide_id, snaps, scraped.get(fide_id, {}),
                     months.get(fide_id, set()))
        for fide_id, snaps in snapshots.items()
        if len(snaps) >= 2
    ]


def load_corrections(conn) -> dict[tuple[int, date], float]:
    with conn.cursor() as cur:
        cur.execute(_LOAD_CORRECTIONS_SQL)
        return {(fide_id, period): float(amount)
                for fide_id, period, amount in cur.fetchall()}


# ── Report ────────────────────────────────────────────────────────────────────

def summarize(all_series: list[PlayerSeries]) -> dict:
    by_rule: dict[str, int] = {}
    unexplained: list[Window] = []
    total = 0
    for s in all_series:
        for w in s.windows:
            total += 1
            if w.status == "explained":
                by_rule[w.explained_by] = by_rule.get(w.explained_by, 0) + 1
            else:
                unexplained.append(w)
    unexplained.sort(key=lambda w: abs(w.residual), reverse=True)
    return {"total": total, "by_rule": by_rule, "unexplained": unexplained}


def print_report(summary: dict, names: dict[int, str] | None = None):
    total = summary["total"]
    unexplained = summary["unexplained"]
    explained = total - len(unexplained)
    names = names or {}

    print()
    print("=" * 64)
    print("  Reconciliation: offizielle Listen vs. gescrapte Partien")
    print("=" * 64)
    print(f"  Fenster gesamt   : {total:>7,}")
    if total:
        print(f"  Erklärt          : {explained:>7,}  ({100 * explained / total:.1f}%)")
        for rule, n in sorted(summary["by_rule"].items(), key=lambda kv: -kv[1]):
            print(f"    · {rule:<18}: {n:>7,}")
        print(f"  Unerklärt        : {len(unexplained):>7,}  "
              f"({100 * len(unexplained) / total:.1f}%)")
    print()

    if unexplained:
        print(f"  Top unerklärte Fenster (max. 20, nach |Residual|):")
        print(f"  {'FIDE-ID':>9} {'Name':<26} {'T1':<10} {'T2':<10} "
              f"{'Exp':>5} {'Scraped':>8} {'Adj':>5} {'Resid':>7} {'Miss':>5}")
        print("  " + "-" * 92)
        for w in unexplained[:20]:
            print(f"  {w.fide_id:>9} {names.get(w.fide_id, '-'):<26.25} "
                  f"{w.period_start} {w.period_end} "
                  f"{w.expected_change:>+5} {w.scraped_change:>+8.1f} "
                  f"{w.adjustment:>+5.0f} {w.residual:>+7.1f} {w.missing_periods:>5}")
    else:
        print("  Keine unerklärten Fenster — Scraping vollständig plausibel.")
    print()


def export_csv(all_series: list[PlayerSeries], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fide_id", "period_start", "period_end", "published_start",
                    "published_end", "expected_change", "scraped_change",
                    "adjustment", "residual", "missing_periods",
                    "status", "explained_by"])
        for s in all_series:
            for win in s.windows:
                w.writerow([win.fide_id, win.period_start, win.period_end,
                            win.published_start, win.published_end,
                            win.expected_change, win.scraped_change,
                            win.adjustment, round(win.residual, 2),
                            win.missing_periods, win.status,
                            win.explained_by or ""])
    print(f"  Alle Fenster exportiert nach {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _connect():
    import psycopg2
    from scraper.config import get_database_url
    return psycopg2.connect(get_database_url())


def cmd_run(args):
    groups = args.group.split(",") if args.group else None
    windows = tuple(int(x) for x in args.windows.split(","))

    conn = _connect()
    try:
        logger.info("Loading player series%s ...",
                    f" for groups {groups}" if groups else "")
        all_series = load_player_series(conn, groups)
        corrections = load_corrections(conn)
        logger.info("  %d players, %d known corrections",
                    len(all_series), len(corrections))

        rules = default_rules(corrections, args.tolerance, windows)
        for s in all_series:
            reconcile(s, rules)

        with conn.cursor() as cur:
            cur.execute("SELECT fide_id, name FROM players WHERE fide_id = ANY(%s)",
                        ([s.fide_id for s in all_series],))
            names = dict(cur.fetchall())
    finally:
        conn.close()

    summary = summarize(all_series)
    print_report(summary, names)
    if args.csv:
        export_csv(all_series, args.csv)


def cmd_verify_names(args):
    groups = args.group.split(",") if args.group else None
    gf, params = _group_filter(groups)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT DISTINCT gr.opponent_name
                FROM game_results gr
                JOIN players p USING (fide_id)
                WHERE gr.opponent_name IS NOT NULL {gf}
                LIMIT %s
            """, params + (args.sample,))
            scraped_names = [r[0] for r in cur.fetchall()]

            cur.execute("""
                SELECT name FROM players
                WHERE name IS NOT NULL AND std_rating IS NOT NULL
            """)
            official = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    logger.info("Matching %d scraped names against %d official entries ...",
                len(scraped_names), len(official))
    matcher = NameMatcher(official)
    results = matcher.match_all(scraped_names)

    buckets: dict[str, list[MatchResult]] = {}
    for r in results:
        buckets.setdefault(r.bucket, []).append(r)

    print()
    print("  Namensabgleich gescrapte Partien ↔ offizielle Liste")
    print("  " + "-" * 52)
    for bucket in (BUCKET_EXACT, BUCKET_HIGH, BUCKET_UNCERTAIN, BUCKET_UNMATCHED):
        n = len(buckets.get(bucket, []))
        print(f"  {bucket:<12}: {n:>6}  ({100 * n / max(len(results), 1):.1f}%)")
    print()
    for r in buckets.get(BUCKET_UNCERTAIN, [])[:20]:
        print(f"    ? '{r.query}' → '{r.matched}' (Score {r.score:.0f})")
    for r in buckets.get(BUCKET_UNMATCHED, [])[:20]:
        print(f"    ✗ '{r.query}' → kein Match")
    print()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Reconciliation über die historischen Daten")
    p_run.add_argument("--group", help="Kommagetrennte analysis_group-Werte")
    p_run.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                       help=f"Toleranz in Elo-Punkten (default {DEFAULT_TOLERANCE})")
    p_run.add_argument("--windows", default=",".join(str(w) for w in DEFAULT_WINDOWS),
                       help="Rollierende Fenster in Monaten (default 2,12)")
    p_run.add_argument("--csv", metavar="FILE", help="Alle Fenster als CSV exportieren")
    p_run.set_defaults(func=cmd_run)

    p_names = sub.add_parser("verify-names", help="Fuzzy-Namensabgleich prüfen")
    p_names.add_argument("--sample", type=int, default=500,
                         help="Anzahl gescrapte Namen (default 500)")
    p_names.add_argument("--group", help="Kommagetrennte analysis_group-Werte")
    p_names.set_defaults(func=cmd_verify_names)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
