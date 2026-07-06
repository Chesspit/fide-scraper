"""Tests for scripts/reconcile_ratings.py — pure logic, no database needed.

Covers the four specified special cases:
1. ±1 tolerance counts as noise
2. month-shift effect resolved by rolling windows
3. fuzzy name matching with surfaced (not dropped) uncertainty
4. rule layer extensible without touching the core
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from reconcile_ratings import (
    BUCKET_EXACT,
    BUCKET_HIGH,
    BUCKET_UNCERTAIN,
    BUCKET_UNMATCHED,
    KnownCorrectionRule,
    NameMatcher,
    NoGameDataRule,
    PlayerSeries,
    RollingWindowRule,
    Rule,
    ToleranceRule,
    Window,
    build_series,
    default_rules,
    reconcile,
    summarize,
)


def _series(windows_spec):
    """Build a PlayerSeries from [(t1, t2, published_start, published_end, scraped)]."""
    s = PlayerSeries(fide_id=1)
    for t1, t2, r1, r2, scraped in windows_spec:
        s.windows.append(Window(
            fide_id=1, period_start=t1, period_end=t2,
            published_start=r1, published_end=r2, scraped_change=scraped,
        ))
    return s


M = lambda month: date(2024, month, 1)


# ── 1. Tolerance ──────────────────────────────────────────────────────────────

class TestTolerance:
    def test_residual_within_tolerance_is_explained(self):
        # expected +5, scraped +4.3 → residual 0.7 → noise
        s = _series([(M(1), M(2), 2000, 2005, 4.3)])
        reconcile(s, [ToleranceRule(1.0)])
        assert s.windows[0].status == "explained"
        assert s.windows[0].explained_by == "tolerance"

    def test_exactly_one_point_is_explained(self):
        s = _series([(M(1), M(2), 2000, 2005, 4.0)])
        reconcile(s, [ToleranceRule(1.0)])
        assert s.windows[0].status == "explained"

    def test_above_tolerance_stays_unexplained(self):
        s = _series([(M(1), M(2), 2000, 2005, 3.0)])  # residual 2.0
        reconcile(s, [ToleranceRule(1.0)])
        assert s.windows[0].status == "unexplained"


# ── 2. Month shift / rolling window ──────────────────────────────────────────

class TestMonthShift:
    def test_shift_into_next_month_explained_by_rolling_2m(self):
        # Tournament scraped in Jan (+10), but the official list applies it in
        # Feb: Jan window residual -10, Feb window residual +10. Individually
        # both fail, cumulatively they cancel.
        s = _series([
            (M(1), M(2), 2000, 2000, 10.0),   # residual -10
            (M(2), M(3), 2000, 2010, 0.0),    # residual +10
        ])
        reconcile(s, default_rules(tolerance=1.0, rolling_windows=(2, 12)))
        assert all(w.status == "explained" for w in s.windows)
        assert all(w.explained_by == "rolling_2m" for w in s.windows)

    def test_longer_shift_needs_the_yearly_window(self):
        # Effect slips three months: only the 12-month window catches it.
        s = _series([
            (M(1), M(2), 2000, 2000, 10.0),   # -10
            (M(2), M(3), 2000, 2000, 0.0),    #   0
            (M(3), M(4), 2000, 2000, 0.0),    #   0
            (M(4), M(5), 2000, 2010, 0.0),    # +10
        ])
        reconcile(s, default_rules(tolerance=1.0, rolling_windows=(2, 12)))
        assert all(w.status == "explained" for w in s.windows)
        assert s.windows[0].explained_by == "rolling_12m"

    def test_true_error_stays_unexplained(self):
        # A one-sided residual never cancels — genuine scraping gap.
        s = _series([
            (M(1), M(2), 2000, 2020, 0.0),    # +20, nothing scraped
            (M(2), M(3), 2020, 2020, 0.0),
        ])
        reconcile(s, default_rules(tolerance=1.0, rolling_windows=(2, 12)))
        assert s.windows[0].status == "unexplained"
        summary = summarize([s])
        assert len(summary["unexplained"]) == 1

    def test_span_limit_prevents_distant_merge(self):
        # -10 and +10 lie 3 months apart with a quiet month in between: the
        # 2-month window must not bridge them (span too wide), a wider window
        # may. Quiet explained months in between never block the bridge.
        s = _series([
            (M(1), M(2), 2000, 2000, 10.0),   # -10
            (M(2), M(3), 2000, 2000, 0.5),    # -0.5 → tolerance
            (M(3), M(4), 2000, 2010, 0.0),    # +10
        ])
        reconcile(s, [ToleranceRule(1.0), RollingWindowRule(2, 1.0)])
        assert s.windows[1].explained_by == "tolerance"
        assert s.windows[0].status == "unexplained"
        assert s.windows[2].status == "unexplained"

        s2 = _series([
            (M(1), M(2), 2000, 2000, 10.0),
            (M(2), M(3), 2000, 2000, 0.5),
            (M(3), M(4), 2000, 2010, 0.0),
        ])
        reconcile(s2, [ToleranceRule(1.0), RollingWindowRule(3, 1.0)])
        assert s2.windows[0].explained_by == "rolling_3m"
        assert s2.windows[2].explained_by == "rolling_3m"
        assert s2.windows[1].explained_by == "tolerance"  # stays as-is


# ── 3. Fuzzy name matching ───────────────────────────────────────────────────

class TestNameMatching:
    OFFICIAL = ["Müller, Hans", "Carlsen, Magnus", "Polgar, Judit"]

    def test_exact_after_normalization(self):
        m = NameMatcher(self.OFFICIAL)
        r = m.match("Hans Müller")          # token order + case differ
        assert r.bucket == BUCKET_EXACT
        assert r.matched == "Müller, Hans"

    def test_diacritics_and_transliteration_high_confidence(self):
        m = NameMatcher(self.OFFICIAL)
        r = m.match("Mueller, Hans")        # ue vs ü
        assert r.bucket in (BUCKET_EXACT, BUCKET_HIGH)
        assert r.matched == "Müller, Hans"

    def test_uncertain_match_is_surfaced_not_dropped(self):
        m = NameMatcher(self.OFFICIAL, high_threshold=95, uncertain_threshold=60)
        r = m.match("Polgar, J.")           # abbreviated first name
        assert r.bucket == BUCKET_UNCERTAIN
        assert r.matched == "Polgar, Judit"  # candidate kept for review

    def test_garbage_is_unmatched(self):
        m = NameMatcher(self.OFFICIAL)
        r = m.match("Xqwzk Vbnm")
        assert r.bucket == BUCKET_UNMATCHED
        assert r.matched is None

    def test_every_query_lands_in_a_bucket(self):
        m = NameMatcher(self.OFFICIAL)
        queries = ["Carlsen Magnus", "carlsen, m.", "???", ""]
        results = m.match_all(queries)
        assert len(results) == len(queries)      # nothing silently discarded
        assert all(r.bucket in (BUCKET_EXACT, BUCKET_HIGH,
                                BUCKET_UNCERTAIN, BUCKET_UNMATCHED)
                   for r in results)


# ── 4. Extensible rule layer ─────────────────────────────────────────────────

class TestRuleExtensibility:
    def test_known_correction_rule_march_2024(self):
        # FIDE one-off: +12 embedded in the 2024-03 list, no games scraped.
        s = _series([(M(2), M(3), 1900, 1912, 0.0)])
        corrections = {(1, date(2024, 3, 1)): 12.0}
        reconcile(s, default_rules(corrections))
        w = s.windows[0]
        assert w.adjustment == 12.0
        assert w.status == "explained"          # residual 0 after adjustment

    def test_custom_rule_plugs_in_without_core_changes(self):
        # New special case discovered later: a known administrative +15
        # adjustment for one player in one period. Adding a named rule
        # suffices — reconcile() and Window stay untouched.
        class AdminAdjustmentRule(Rule):
            name = "admin_adjustment_2024_06"

            def adjust(self, window):
                if (window.fide_id == 1
                        and window.period_start < date(2024, 6, 1) <= window.period_end):
                    return 15.0
                return 0.0

        s = _series([(M(5), M(6), 2000, 2015, 0.0)])   # unexplained without rule
        rules = default_rules() + [AdminAdjustmentRule()]
        reconcile(s, rules)
        assert s.windows[0].status == "explained"

    def test_no_game_data_rule_pre_2008(self):
        # Official lists exist before 2008-04, game data does not: a +113
        # jump in early 2006 is unverifiable, not a scraping error.
        s = _series([(date(2006, 4, 1), date(2006, 7, 1), 2400, 2513, 0.0)])
        reconcile(s, default_rules())
        assert s.windows[0].status == "explained"
        assert s.windows[0].explained_by == "no_game_data"

    def test_no_game_data_rule_leaves_2008_04_alone(self):
        # The window ending 2008-04 contains the first real game period and
        # must still be reconciled normally.
        s = _series([(date(2008, 1, 1), date(2008, 4, 1), 2400, 2450, 0.0)])
        reconcile(s, default_rules())
        assert s.windows[0].status == "unexplained"

    def test_custom_explain_rule(self):
        # Rules can also explain (not just adjust): mark windows of a known
        # broken FIDE list as accepted.
        class BrokenListRule(Rule):
            name = "broken_list_2024_04"

            def explain(self, series):
                for w in series.windows:
                    if w.status == "unexplained" and w.period_end == date(2024, 4, 1):
                        w.status = "explained"
                        w.explained_by = self.name

        s = _series([(M(3), M(4), 2000, 2050, 0.0)])
        reconcile(s, default_rules() + [BrokenListRule()])
        assert s.windows[0].explained_by == "broken_list_2024_04"


# ── build_series plumbing ────────────────────────────────────────────────────

class TestBuildSeries:
    def test_windows_from_consecutive_snapshots(self):
        snaps = [(M(1), 2000), (M(2), 2010), (M(4), 2015)]  # gap: no March list
        scraped = {M(2): 10.0, M(3): 2.0, M(4): 3.0}
        s = build_series(1, snaps, scraped)
        assert len(s.windows) == 2
        # second window spans two months — both game sums included
        assert s.windows[1].scraped_change == 5.0
        assert s.windows[1].expected_change == 5

    def test_quarterly_pre2012_window_needs_no_special_case(self):
        snaps = [(date(2011, 1, 1), 2000), (date(2011, 3, 1), 2020)]
        scraped = {date(2011, 2, 1): 8.0, date(2011, 3, 1): 12.0}
        s = build_series(1, snaps, scraped)
        assert s.windows[0].scraped_change == 20.0
        reconcile(s, default_rules())
        assert s.windows[0].status == "explained"

    def test_missing_periods_respects_fide_calendar(self):
        # 2011: bi-monthly lists (Jan/Mar/May/...) — Feb 2011 is structurally
        # empty and must not count as missing.
        snaps = [(date(2011, 1, 1), 2000), (date(2011, 3, 1), 2000)]
        s = build_series(1, snaps, {}, scraped_months={date(2011, 3, 1)})
        assert s.windows[0].missing_periods == 0
