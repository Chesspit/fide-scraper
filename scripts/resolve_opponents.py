#!/usr/bin/env python3
"""Resolve opponent_fide_id in game_results by looking up name + federation in players.

Two-stage matching strategy:

1. **Exact match (normalized name + federation)** — applied without rating tolerance
   when a single candidate exists. Handles the common case where the opponent played
   under the same federation they currently hold.
2. **Fed-fallback (normalized name only)** — applied when step 1 misses, for
   opponents whose federation changed (Caruana ITA→USA, Aronian ARM→USA, Svidler
   RUS→FID, etc.). Requires the candidate's rating at the time of play to be within
   `--fed-fallback-tolerance` (default 100) of the game's opponent_rating.

3. **Fuzzy (opt-in, --fuzzy)** — rapidfuzz on names, rating-bounded.

Period-accurate rating disambiguation:
    Candidates are compared by their `rating_history.published_rating` on the
    previous list (fallback: list P, then `players.std_rating`).

Periode für Periode (seit 11/2026): je Periode die offenen Partien lesen, zuordnen,
und mit EINEM UPDATE opponent_fide_id plus die abgeleiteten Spalten schreiben
(opponent_sex, opponent_match_quality, expected_score/over_performance,
tournament_type; Regeln wie Migration 007/009, Turniertyp per fn_tournament_type
aus Migration 026). Commit je Periode → abbrechbar und wiederaufnehmbar.

    python scripts/resolve_opponents.py --since 2020-01 --fuzzy
    python scripts/resolve_opponents.py --period 2026-06 --fuzzy --dry-run

Name normalization (lowercase, drop commas, collapse whitespace) handles the
FIDE-TXT convention differences that plague Indian names: calc-HTML writes
"Gukesh, D" while the TXT has "Gukesh D" — same player, different format.
"""

import argparse
import io
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date

import psycopg2

try:
    from rapidfuzz import fuzz as _fuzz
except ImportError:
    _fuzz = None


from core.config import get_database_url
from core.elo import diff_cap
from core.periods import (is_valid_fide_period, last_complete_period, parse_period,
                          previous_period)

logger = logging.getLogger(__name__)


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str | None) -> str:
    """Lowercase, replace any non-alphanumeric run with a single space.

    Matches:
      "Gukesh, D" ↔ "Gukesh D"
      "Tabatabaei, M.amin" ↔ "Tabatabaei, M. Amin"
      "L`ami, Erwin" ↔ "L'Ami, Erwin"
      "Daulyte-Cornette, Deimante" ↔ "Daulyte Cornette Deimante"
    """
    if not name:
        return ""
    cleaned = _NON_ALNUM_RE.sub(" ", name.lower())
    return cleaned.strip()


def build_candidate_maps(conn) -> tuple[dict, dict]:
    """Load the players table once, return two lookup maps.

    - `by_name_fed`: {(normalized_name, federation): [(fide_id, std_rating), ...]}
    - `by_name`:     {normalized_name: [(fide_id, std_rating, federation), ...]}

    Loading 1.8M rows into memory is cheaper than N per-row lookups.
    """
    by_name_fed: dict = defaultdict(list)
    by_name: dict = defaultdict(list)

    with conn.cursor(name="players_stream") as cur:
        cur.itersize = 50000
        cur.execute(
            "SELECT fide_id, name, federation, std_rating FROM players WHERE name IS NOT NULL"
        )
        for fide_id, name, fed, rating in cur:
            norm = normalize_name(name)
            if not norm:
                continue
            if fed:
                by_name_fed[(norm, fed)].append((fide_id, rating))
            by_name[norm].append((fide_id, rating, fed))

    return by_name_fed, by_name


def load_period_ratings(conn, period: date) -> dict[int, int]:
    """published_rating der Kandidaten, wie es zur Zeit der Partien in `period` galt.

    Die Berechnungsseite für Periode P zeigt das Gegner-Rating zur Turnierzeit, also
    meist das der Vorliste. Geladen werden Vorliste und Liste P (Index aus Migration
    023); die Vorliste gewinnt, Liste P füllt nur Lücken (z. B. Erstbewertungen).
    Früher wurde die ganze rating_history (61 Mio Zeilen) in den Speicher geladen.
    """
    ratings: dict[int, int] = {}
    with conn.cursor(name="rh_period") as cur:
        cur.itersize = 100_000
        cur.execute(
            """
            SELECT fide_id, published_rating
            FROM rating_history
            WHERE period IN (%s, %s) AND published_rating > 0
            ORDER BY (period = %s) DESC
            """,
            (period, previous_period(period), period),
        )
        for fide_id, rating in cur:
            ratings[fide_id] = rating
    return ratings


def make_rating_fn(period_ratings: dict[int, int]):
    """rating_fn(fide_id, std_rating) → Rating zur Periode, sonst heutiges std_rating.

    Mit period_only=True nur das Rating aus den Listen der Periode (oder None) —
    für Prüfungen, bei denen das heutige Rating irreführen würde.
    """
    def rating_fn(fide_id: int, std_rating: int | None, period_only: bool = False) -> int | None:
        r = period_ratings.get(fide_id)
        if r is not None or period_only:
            return r
        return std_rating if std_rating else None
    return rating_fn


def make_gap(opp_rating: int | None, ro=None, cap: int | None = None):
    """gap(r) → Abstand zwischen Kandidaten-Rating r und dem Gegner-Rating der Partie.

    FIDE zeigt das Gegner-Rating nach der 400-Punkte-Regel gekappt (eigenes Rating
    ± 400). Steht der Wert genau auf der Kappungsgrenze, ist jedes Rating jenseits davon
    ebenso plausibel (Beispiel: eigenes Rating 2307, angezeigt 1907, echter Gegner 1404).
    ro: eigenes Rating oder mehrere Kandidaten dafür (Vorliste, Ro der Zusammenfassung —
    sie weichen ab, wenn mehrere Turniere in der Periode liegen). None, wenn die Partie
    kein Gegner-Rating hat.
    """
    if opp_rating is None:
        return None
    owns = [x for x in (ro if isinstance(ro, (list, tuple)) else [ro]) if x]
    lower = upper = False
    if cap:
        lower = any(abs(opp_rating - (o - cap)) <= 1 for o in owns)  # Gegner war schwächer
        upper = any(abs(opp_rating - (o + cap)) <= 1 for o in owns)  # Gegner war stärker

    def gap(r: int) -> int:
        if lower and r < opp_rating:
            return 0
        if upper and r > opp_rating:
            return 0
        return abs(r - opp_rating)
    return gap


def pick_closest(
    cands: list[tuple],
    gap,
    rating_fn,
    max_diff: int | None = None,
) -> tuple[int | None, float]:
    """Aus Kandidaten [(fide_id, std_rating, ...)] den mit dem kleinsten Abstand
    (gap, siehe make_gap) zum Gegner-Rating der Partie wählen.
    (fide_id, diff); bei diff > max_diff (None, diff)."""
    if gap is None:
        return None, float("inf")

    best_id = None
    best_diff = float("inf")
    for cand in cands:
        rating_then = rating_fn(cand[0], cand[1])
        if rating_then is None:
            continue
        diff = gap(rating_then)
        if diff < best_diff:
            best_id = cand[0]
            best_diff = diff
    if best_id is not None and max_diff is not None and best_diff > max_diff:
        return None, best_diff
    return best_id, best_diff


def build_token_index(by_name: dict, min_token_len: int = 3) -> dict[str, set]:
    """Inverted index: token -> set of normalized names containing that token.

    Only tokens of length >= min_token_len (default 3) are indexed, to skip
    initials and single letters that would blow up the candidate pool.
    """
    idx: dict[str, set] = defaultdict(set)
    for norm_name in by_name:
        for tok in norm_name.split():
            if len(tok) >= min_token_len:
                idx[tok].add(norm_name)
    return idx


def fuzzy_match(
    opp_norm: str,
    gap,
    rating_fn,
    token_index: dict,
    by_name: dict,
    score_threshold: int,
    rating_tolerance: int,
    surname: str = "",
) -> tuple[int | None, int, float, str | None]:
    """Stage-3 fuzzy match. Returns (fide_id, score, rating_diff, matched_name).

    Candidate generation: intersect the token-inverted-index lookups across
    all tokens in `opp_norm` of length >= 3. This gives names sharing every
    long token with the opponent (handles extra-token case like
    "Jones Gawain C B" ⊆ "Maroroa Jones Gawain C B").

    Scoring: rapidfuzz WRatio (combines token_set, token_sort, partial_ratio,
    ratio). Candidates below `score_threshold` are dropped.

    Disambiguation: period-accurate rating within `rating_tolerance` Elo.
    Tiebreaker: higher score, then lower rating diff.
    """
    if _fuzz is None:
        return None, 0, -1.0, None
    if gap is None:
        return None, 0, -1.0, None

    tokens = [t for t in opp_norm.split() if len(t) >= 3]
    if not tokens:
        return None, 0, -1.0, None

    # Pass A: intersect — all long tokens must appear in candidate.
    # Handles field-swap, extra-token, word-order variants.
    cand_names: set | None = None
    missing_token = False
    for tok in tokens:
        names = token_index.get(tok)
        if not names:
            missing_token = True
            break
        cand_names = names.copy() if cand_names is None else cand_names & names
        if not cand_names:
            break

    # Pass B: fallback — intersection empty (transliteration like
    # Ivanchuk Vassily↔Vasyl, abbreviation like Harikrishna P↔Pentala).
    # Use the RAREST token as anchor (distinctive surname, not a common
    # given name like 'andrey'). Using a frequent token as anchor
    # produces false positives (e.g. Vovk Andrey → Baryshpolets Andrey).
    if not cand_names or missing_token:
        anchor = min(tokens, key=lambda t: len(token_index.get(t, ())))
        cand_names = token_index.get(anchor, set())
        if not cand_names:
            return None, 0, -1.0, None

    best_id = None
    best_score = 0
    best_diff: float = float("inf")
    best_name = None

    surname_tokens = [t for t in surname.split() if len(t) >= 3]
    for name in cand_names:
        # Der Kandidat muss mindestens ein Nachnamens-Token (oder dessen Anfang)
        # enthalten: verhindert „Tello Chavez, Isaac Valent" → „Valent, Ivan".
        if surname_tokens and not any(
                ct.startswith(st) or st.startswith(ct)
                for st in surname_tokens for ct in name.split() if len(ct) >= 3):
            continue
        score = _fuzz.WRatio(opp_norm, name)
        if score < score_threshold:
            continue
        for cand in by_name[name]:
            fide_id = cand[0]
            rating_then = rating_fn(fide_id, cand[1])
            if rating_then is None:
                continue
            diff = gap(rating_then)
            if diff > rating_tolerance:
                continue
            # Prefer higher score; tie-break on lower rating diff
            if score > best_score or (score == best_score and diff < best_diff):
                best_id = fide_id
                best_score = score
                best_diff = diff
                best_name = name

    return best_id, best_score, best_diff if best_id is not None else -1.0, best_name


class Matcher:
    """Spielerliste einmal laden, dann Partien Periode für Periode zuordnen."""

    def __init__(self, conn, fuzzy: bool, fed_fallback_tolerance: int, exact_tolerance: int | None,
                 fuzzy_threshold: int, fuzzy_rating_tolerance: int):
        self.fuzzy = fuzzy
        self.fed_fallback_tolerance = fed_fallback_tolerance
        self.exact_tolerance = exact_tolerance
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_rating_tolerance = fuzzy_rating_tolerance

        logger.info("Loading players table and building normalized candidate maps...")
        self.by_name_fed, self.by_name = build_candidate_maps(conn)
        logger.info("  %d (name, fed) buckets, %d name-only buckets",
                    len(self.by_name_fed), len(self.by_name))
        self.token_index: dict = {}
        if fuzzy:
            if _fuzz is None:
                logger.error("--fuzzy requested but rapidfuzz is not installed")
                sys.exit(1)
            logger.info("Building token-inverted index for fuzzy stage...")
            self.token_index = build_token_index(self.by_name)
            logger.info("  %d indexed tokens (len>=3)", len(self.token_index))

    def match(self, opp_name, opp_fed, opp_rating, rating_fn,
              ro=None, cap: int | None = None) -> tuple[int | None, str]:
        """→ (fide_id, Stufe) mit Stufe exact | fallback | fuzzy | ambiguous | none.

        ro = eigenes Rating des gescrapten Spielers (Wert oder Tupel aus Vorliste und
        Ro), cap = 400-Punkte-Regel der Partie; beide nur für die Kappung (make_gap).
        """
        norm = normalize_name(opp_name)
        gap = make_gap(opp_rating, ro, cap)

        # Stufe 1: Name + Föderation
        cands = self.by_name_fed.get((norm, opp_fed), [])
        if cands:
            if len(cands) == 1:
                # Einziger Namensträger der Föderation: nur übernehmen, wenn das Rating
                # passt oder sich nicht prüfen lässt. Sonst ist es ein Namensvetter und der
                # Gesuchte hat die Föderation gewechselt (Fedoseev RUS→SLO) → Stufe 2.
                fid, std = cands[0][0], cands[0][1]
                r = rating_fn(fid, std)
                if gap is None or r is None or gap(r) <= self.exact_tolerance:
                    return fid, "exact"
            else:
                best_id, _ = pick_closest(cands, gap, rating_fn,
                                          max_diff=self.exact_tolerance)
                if best_id is not None:
                    return best_id, "exact"

        # Stufe 2: nur Name (Föderationswechsel), Rating-begrenzt
        cands_any_fed = self.by_name.get(norm, [])
        if cands_any_fed:
            best_id, _ = pick_closest(cands_any_fed, gap, rating_fn,
                                      max_diff=self.fed_fallback_tolerance)
            if best_id is not None:
                return best_id, "fallback"

        # Stufe 3: unscharf (Feldtausch, Zusatznamen, Transliteration), Rating-begrenzt.
        # Nicht, wenn es exakte Namensträger gab, die nur am Rating scheiterten: dann
        # fände die Unschärfe bloß einen fremden Namen mit passendem Rating (Nikhil, Gopal).
        if self.fuzzy and not cands and not cands_any_fed:
            fz_id, _, _, _ = fuzzy_match(
                norm, gap, rating_fn, self.token_index, self.by_name,
                surname=normalize_name((opp_name or "").split(",")[0]),
                score_threshold=self.fuzzy_threshold,
                rating_tolerance=self.fuzzy_rating_tolerance,
            )
            if fz_id is not None:
                return fz_id, "fuzzy"

        return None, ("ambiguous" if cands or cands_any_fed else "none")


# Ein UPDATE je Periode setzt alle abgeleiteten Spalten auf einmal (eine neue
# Zeilenversion statt fünf). Regeln wie Migration 007/009:
#   opponent_sex           players.sex, sonst 'F' bei Frauentitel
#   opponent_match_quality ok / wide_gap (|Gegner-Rating − players.std_rating| > 200) / unresolved
#   expected_score         logistische Erwartung gegen Ro (rating_history.std_rating)
#   tournament_type        fn_tournament_type (Migration 026)
# Nur Zeilen mit neuer Zuordnung oder fehlender Anreicherung werden angefasst,
# ein zweiter Lauf ist daher billig.
_ENRICH_SQL = """
UPDATE game_results gr
SET opponent_fide_id       = n.new_fid,
    opponent_sex           = COALESCE(n.sex, CASE WHEN gr.opponent_women_title IS NOT NULL
                                                  THEN 'F' END),
    opponent_match_quality = CASE
        WHEN n.new_fid IS NULL THEN 'unresolved'
        WHEN ABS(gr.opponent_rating - n.opp_std_rating) > 200 THEN 'wide_gap'
        ELSE 'ok' END,
    expected_score         = ROUND(1.0 / (1.0 + POWER(10.0,
                                 (gr.opponent_rating - n.ro) / 400.0)), 4),
    over_performance       = ROUND(CAST(gr.result AS NUMERIC) - 1.0 / (1.0 + POWER(10.0,
                                 (gr.opponent_rating - n.ro) / 400.0)), 4),
    tournament_type        = fn_tournament_type(gr.tournament_name)
FROM (
    SELECT g.id,
           COALESCE(g.opponent_fide_id, m.opponent_fide_id) AS new_fid,
           p.sex, p.std_rating AS opp_std_rating, rh.std_rating AS ro
    FROM game_results g
    LEFT JOIN _resolved m ON m.id = g.id
    LEFT JOIN players p ON p.fide_id = COALESCE(g.opponent_fide_id, m.opponent_fide_id)
    LEFT JOIN rating_history rh ON rh.fide_id = g.fide_id AND rh.period = g.period
    WHERE g.period = %(period)s
      AND (m.id IS NOT NULL
           OR g.opponent_match_quality IS NULL
           OR g.tournament_type IS NULL)
) n
WHERE gr.id = n.id
  AND gr.period = %(period)s
"""


def periods_to_process(period: str | None, since: str | None, until: str | None) -> list[date]:
    if period:
        return [parse_period(period)]
    start = parse_period(since) if since else date(2008, 4, 1)
    end = parse_period(until) if until else last_complete_period()
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        if is_valid_fide_period(date(y, m, 1)):
            out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def resolve_period(conn, matcher: Matcher, period: date, dry_run: bool) -> Counter:
    """Eine Periode: offene Gegner zuordnen und alle Anreicherungsspalten schreiben."""
    stats: Counter = Counter()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.id, g.opponent_name, g.opponent_federation, g.opponent_rating,
                   rh.std_rating, pv.published_rating, g.tournament_start_date
            FROM game_results g
            LEFT JOIN rating_history rh ON rh.fide_id = g.fide_id AND rh.period = g.period
            LEFT JOIN rating_history pv ON pv.fide_id = g.fide_id AND pv.period = %s
            WHERE g.period = %s AND g.opponent_fide_id IS NULL
            """,
            (previous_period(period), period),
        )
        rows = cur.fetchall()
    stats["unresolved_before"] = len(rows)

    updates: list[tuple[int, int]] = []
    if rows:
        rating_fn = make_rating_fn(load_period_ratings(conn, period))
        for row_id, opp_name, opp_fed, opp_rating, ro, own_prev, t_start in rows:
            cap = diff_cap(t_start or period)
            fid, stage = matcher.match(opp_name, opp_fed, opp_rating, rating_fn,
                                       (own_prev, ro), cap)
            stats[stage] += 1
            if fid is not None:
                updates.append((row_id, fid))

    if dry_run:
        conn.rollback()
        return stats

    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS _resolved "
                    "(id bigint PRIMARY KEY, opponent_fide_id integer) ON COMMIT DELETE ROWS")
        if updates:
            buf = io.StringIO("".join(f"{i}\t{f}\n" for i, f in updates))
            cur.copy_expert("COPY _resolved (id, opponent_fide_id) FROM STDIN", buf)
            cur.execute("ANALYZE _resolved")
        cur.execute(_ENRICH_SQL, {"period": period})
        stats["rows_written"] = cur.rowcount
    conn.commit()
    return stats


def resolve_opponents(conn, periods: list[date], dry_run: bool, pause: float, **matcher_kw):
    matcher = Matcher(conn, **matcher_kw)
    total: Counter = Counter()
    for i, period in enumerate(periods, 1):
        t0 = time.monotonic()
        st = resolve_period(conn, matcher, period, dry_run)
        total.update(st)
        matched = st["exact"] + st["fallback"] + st["fuzzy"]
        before = st["unresolved_before"]
        logger.info(
            "[%d/%d] %s: %d offen, %d zugeordnet (%.1f %%; exact %d, fallback %d, fuzzy %d), "
            "%d mehrdeutig, %d ohne Treffer, %d Zeilen geschrieben, %.0f s",
            i, len(periods), period, before, matched,
            100 * matched / before if before else 0.0,
            st["exact"], st["fallback"], st["fuzzy"], st["ambiguous"], st["none"],
            st["rows_written"], time.monotonic() - t0,
        )
        if pause and not dry_run and i < len(periods):
            time.sleep(pause)

    matched = total["exact"] + total["fallback"] + total["fuzzy"]
    before = total["unresolved_before"]
    print(f"\n{'Dry-run' if dry_run else 'Fertig'}: {len(periods)} Perioden")
    print(f"  offen vorher:   {before}")
    print(f"  zugeordnet:     {matched} ({100 * matched / before if before else 0:.1f} %)"
          f"  exact {total['exact']}, fallback {total['fallback']}, fuzzy {total['fuzzy']}")
    print(f"  mehrdeutig:     {total['ambiguous']}")
    print(f"  ohne Treffer:   {total['none']}")
    if not dry_run:
        print(f"  Zeilen geschrieben (inkl. Anreicherung): {total['rows_written']}")

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Resolve opponent FIDE IDs via name+federation lookup"
    )
    parser.add_argument("--period", type=str, help="Nur diese Periode (YYYY-MM)")
    parser.add_argument("--since", type=str, help="Ab Periode (YYYY-MM, Default 2008-04)")
    parser.add_argument("--until", type=str, help="Bis Periode (YYYY-MM, Default Vormonat)")
    parser.add_argument("--pause", type=float, default=2.0,
                        help="Sekunden Pause zwischen Perioden (Autovacuum/Worker Luft lassen)")
    parser.add_argument("--dry-run", action="store_true", help="Count only, don't write")
    parser.add_argument(
        "--fed-fallback-tolerance",
        type=int,
        default=100,
        help="Max |rating diff| when matching across federations (default 100, "
             "period-accurate ratings make this tight)",
    )
    parser.add_argument(
        "--exact-tolerance",
        type=int,
        default=200,
        help="Max |rating diff| for exact (name+fed) matches, period-accurate. "
             "Default 200; a single candidate is only checked when a rating is known.",
    )
    parser.add_argument(
        "--fuzzy",
        action="store_true",
        help="Enable Stage-3 fuzzy matching (handles name field-order swap, "
             "extra surname tokens, transliteration). Requires rapidfuzz.",
    )
    parser.add_argument(
        "--fuzzy-threshold",
        type=int,
        default=83,
        help="Minimum rapidfuzz WRatio for fuzzy matches (default 83). "
             "Empirically: 85 catches transliteration + hyphenation, 83 adds "
             "abbreviation cases (Shankland Samuel L↔Sam), 80 adds noise.",
    )
    parser.add_argument(
        "--fuzzy-rating-tolerance",
        type=int,
        default=100,
        help="Max |rating diff| for fuzzy matches, period-accurate (default 100)",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(
        get_database_url("ingest"),
        options="-c statement_timeout=900000",  # 15 min, see scraper/db.py
    )
    try:
        resolve_opponents(
            conn,
            periods_to_process(args.period, args.since, args.until),
            dry_run=args.dry_run,
            pause=args.pause,
            fuzzy=args.fuzzy,
            fed_fallback_tolerance=args.fed_fallback_tolerance,
            exact_tolerance=args.exact_tolerance,
            fuzzy_threshold=args.fuzzy_threshold,
            fuzzy_rating_tolerance=args.fuzzy_rating_tolerance,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
