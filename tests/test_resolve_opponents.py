"""Tests für scripts/resolve_opponents.py — Periodenliste und Rating-Abgleich (ohne DB)."""

from datetime import date

from scripts.resolve_opponents import (
    Matcher,
    make_rating_fn,
    normalize_name,
    periods_to_process,
    pick_closest,
)


def test_periods_single():
    assert periods_to_process("2026-06", None, None) == [date(2026, 6, 1)]


def test_periods_range_respects_fide_calendar():
    got = periods_to_process(None, "2012-05", "2012-09")
    # vor 2012-08 nur ungerade Monate, danach monatlich
    assert got == [date(2012, 5, 1), date(2012, 7, 1), date(2012, 8, 1), date(2012, 9, 1)]


def test_rating_fn_prefers_period_rating_then_std():
    fn = make_rating_fn({1: 2100})
    assert fn(1, 1800) == 2100
    assert fn(2, 1800) == 1800
    assert fn(3, 0) is None          # std_rating 0 = unbewertet, kein Vergleichswert


def test_pick_closest_uses_period_rating_and_limit():
    fn = make_rating_fn({10: 1500, 11: 2210})
    cands = [(10, 2200), (11, 1500)]  # heutige Ratings irreführend
    assert pick_closest(cands, 2200, fn) == (11, 10)
    assert pick_closest(cands, 1900, fn, max_diff=100)[0] is None


def _matcher(by_name_fed, by_name) -> Matcher:
    m = Matcher.__new__(Matcher)
    m.by_name_fed, m.by_name = by_name_fed, by_name
    m.fuzzy, m.token_index = False, {}
    m.fed_fallback_tolerance, m.exact_tolerance = 100, 300
    return m


def test_match_stages():
    norm = normalize_name("Gukesh, D")
    m = _matcher({(norm, "IND"): [(46616543, 2750)]},
                 {norm: [(46616543, 2750, "IND")]})
    fn = make_rating_fn({})
    assert m.match("Gukesh D", "IND", 2700, fn) == (46616543, "exact")
    assert m.match("Gukesh, D", "FID", 2700, fn) == (46616543, "fallback")
    assert m.match("Gukesh, D", "FID", 2000, fn) == (None, "ambiguous")   # Name da, Rating zu weit weg
    assert m.match("Unbekannt, X", "GER", 2000, fn) == (None, "none")


def test_single_namesake_with_wrong_rating_falls_through_to_other_federation():
    """Fedoseev: GM spielt heute für SLO, in RUS bleibt nur ein Namensvetter mit 2152."""
    norm = normalize_name("Fedoseev, Vladimir")
    m = _matcher({(norm, "RUS"): [(4131797, 2152)], (norm, "SLO"): [(24130737, 2691)]},
                 {norm: [(4131797, 2152, "RUS"), (24130737, 2691, "SLO")]})
    fn = make_rating_fn({4131797: 2150, 24130737: 2675})
    assert m.match("Fedoseev, Vladimir", "RUS", 2672, fn) == (24130737, "fallback")
    assert m.match("Fedoseev, Vladimir", "RUS", 2140, fn) == (4131797, "exact")
