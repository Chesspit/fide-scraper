"""Tests für die zentrale ELO-Band-Klassifikation.

Zwei Implementierungen müssen übereinstimmen:
  * SQL  — fn_elo_band()/fn_elo_group() aus migrations/017_elo_band_function.sql
  * pandas — notebooks/_setup.py::elo_band_floor()/elo_band()

Sie liegen bewusst doppelt vor (SQL für Coverage/QC-Abfragen, pandas für die
Notebooks, die ihre Daten ohnehin schon im DataFrame haben). Genau deshalb
prüft der letzte Test beide Seiten gegeneinander — vor der Zentralisierung war
die Bandlogik in _generate_13.py und _generate_14.py wortgleich dupliziert und
hätte jederzeit auseinanderlaufen können.

Die Migration wird hier aus der Datei eingespielt statt im Test dupliziert:
so schlägt der Test an, wenn jemand die SQL-Datei ändert, ohne den pandas-Helper
nachzuziehen.
"""

import sys
from pathlib import Path

import pytest

from orchestrator.setup_db import connect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "notebooks"))
from _setup import elo_band, elo_band_floor  # noqa: E402

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "017_elo_band_function.sql"
)


@pytest.fixture
def conn(data_db):
    c = connect()
    with c.cursor() as cur:
        cur.execute(MIGRATION.read_text())
    yield c
    c.close()


def _scalar(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


class TestFnEloBand:
    @pytest.mark.parametrize("rating,expected", [
        (2449, 2400),   # oberer Rand des Bands
        (2450, 2450),   # unterer Rand des naechsten
        (2400, 2400),
        (1850, 1850),
        (2000, 2000),
        (1, 0),
        (0, 0),
    ])
    def test_floor(self, conn, rating, expected):
        assert _scalar(conn, "SELECT fn_elo_band(%s)", rating) == expected

    def test_null_stays_null(self, conn):
        assert _scalar(conn, "SELECT fn_elo_band(NULL::integer)") is None


class TestFnEloGroup:
    @pytest.mark.parametrize("rating,sex,expected", [
        (2455, "F", "f_2450_2499"),
        (2450, "M", "m_2450_2499"),
        (2449, "F", "f_2400_2449"),
        (1850, "m", "m_1850_1899"),   # Kleinschreibung wird normalisiert
        (1850, "f", "f_1850_1899"),
    ])
    def test_labels(self, conn, rating, sex, expected):
        assert _scalar(conn, "SELECT fn_elo_group(%s, %s)", rating, sex) == expected

    @pytest.mark.parametrize("sex", [None, "X", "?"])
    def test_unknown_sex_becomes_x_prefix(self, conn, sex):
        """Unbekanntes Geschlecht darf nicht zu NULL werden — sonst fallen die
        betroffenen Spieler (~0,1 %) stillschweigend aus jeder Gruppierung."""
        assert _scalar(conn, "SELECT fn_elo_group(2000, %s)", sex) == "x_2000_2049"

    def test_null_rating_stays_null(self, conn):
        assert _scalar(conn, "SELECT fn_elo_group(NULL::integer, 'F')") is None


class TestSqlMatchesPandas:
    """Der eigentliche Zweck: SQL und pandas dürfen nie auseinanderlaufen."""

    def test_floors_agree_across_range(self, conn):
        ratings = list(range(1000, 2800, 7)) + [0, 1, 49, 50, 2449, 2450, 2599, 2600]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT r, fn_elo_band(r) FROM unnest(%s::integer[]) AS t(r)",
                (ratings,),
            )
            sql_floors = dict(cur.fetchall())
        mismatches = [
            (r, sql_floors[r], elo_band_floor(r))
            for r in ratings
            if sql_floors[r] != elo_band_floor(r)
        ]
        assert not mismatches, f"SQL/pandas weichen ab: {mismatches[:10]}"

    def test_pandas_label_derives_from_same_floor(self, conn):
        for rating in (2449, 2450, 1850, 0):
            floor = _scalar(conn, "SELECT fn_elo_band(%s)", rating)
            assert elo_band(rating) == f"{floor}-{floor + 49}"

    def test_pandas_label_handles_missing(self):
        assert elo_band(None) == "unknown"
