"""Rating-Spalte in Listenköpfen: auch die Ausreißer-Schreibweise "July11" (jul11frl.txt)."""
import pytest

from core.fide_list import MONTH_RATING_PATTERN, detect_columns_from_header

PRE2013 = "ID number Name                              TitlFed  {m} GamesBorn  Flag"


@pytest.mark.parametrize("label", ["May10", "sep09", "Jul11", "July11", "JUN14", "FEB15"])
def test_month_label_found_at_its_start(label):
    header = PRE2013.format(m=label.ljust(6))
    m = MONTH_RATING_PATTERN.search(header)
    assert m and m.start() == header.index(label)


def test_july11_header_gets_rating_column():
    cols = detect_columns_from_header(PRE2013.format(m="July11"))
    assert cols["std_rating"] == (53, 58)


def test_july11_games_column_follows_rating_not_header():
    # Kopf um eins verschoben ("July11"), Datenzeilen nicht: Games = Rating + 6
    cols = detect_columns_from_header(PRE2013.format(m="July11"))
    assert cols["std_games"] == (59, 64)
    assert detect_columns_from_header(PRE2013.format(m="May10 "))["std_games"] == (59, 64)
