"""Einordnung einer Listen-Revision (scripts/apply_list_revision.py)."""

from scripts.apply_list_revision import classify, rated, rescrape_ids


def test_rated_ignores_unrated():
    players = [{"fide_id": 1, "std_rating": 1500, "std_games": 3},
               {"fide_id": 2, "std_rating": 0, "std_games": None}]
    assert rated(players) == {1: (1500, 3)}


def test_classify_cases():
    old = {1: (1500, 3), 2: (1600, 0), 3: (1700, 5), 4: (1800, 4), 5: (1900, 2), 6: (2000, 1)}
    new = {1: (1501, 3),               # nur Rating
           2: (1600, 2),               # 0 → >0
           3: (1700, 0),               # >0 → 0
           4: (1810, 6),               # Partienzahl + Rating anders
           5: (1900, 2),               # unverändert
           7: (1400, 5), 8: (1300, None)}  # neu mit / ohne Partien; 6 entfernt
    c = classify(old, new)
    assert c["rating_only"] == [1]
    assert c["games_up"] == [2]
    assert c["games_down"] == [3]
    assert c["games_other"] == [4]
    assert c["added"] == [7]
    assert c["added_nogames"] == [8]
    assert c["removed"] == [6]
    assert rescrape_ids(c) == [2, 3, 4, 7]
