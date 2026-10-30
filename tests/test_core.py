"""Tests für core/ — Periodenkalender, Elo-Zwillinge (ohne DB), DB-URL-Auflösung."""

import math
from datetime import date

import pytest

from core import config
from core.elo import elo_band, elo_band_floor, fide_expected
from core.periods import (
    in_latest_list_sql,
    is_valid_fide_period,
    last_complete_period,
    parse_period,
    previous_period,
    valid_periods_for_year,
)


class TestPeriods:
    def test_calendar(self):
        assert not is_valid_fide_period(date(2008, 1, 1))
        assert [m for m in range(1, 13) if is_valid_fide_period(date(2008, m, 1))] == [4, 7, 10]
        assert [m for m in range(1, 13) if is_valid_fide_period(date(2009, m, 1))] == [1, 4, 7, 9, 11]
        assert is_valid_fide_period(date(2012, 8, 1)) and not is_valid_fide_period(date(2012, 6, 1))

    def test_cutoff_is_previous_month(self):
        assert last_complete_period(date(2026, 1, 15)) == date(2025, 12, 1)
        assert valid_periods_for_year(2026, today=date(2026, 4, 2)) == [
            "2026-01-01", "2026-02-01", "2026-03-01"]
        assert valid_periods_for_year(2027, today=date(2026, 4, 2)) == []

    def test_previous_period_quarterly(self):
        assert previous_period(date(2009, 1, 1)) == date(2008, 10, 1)
        assert previous_period(date(2012, 9, 1)) == date(2012, 8, 1)
        assert previous_period(date(2012, 8, 1)) == date(2012, 7, 1)

    def test_parse_period(self):
        assert parse_period("2020-01") == parse_period("2020-01-01") == date(2020, 1, 1)
        with pytest.raises(ValueError):
            parse_period("2020")

    def test_in_latest_sql_placeholder(self):
        sql = in_latest_list_sql("rh", "%(latest)s")
        assert "rh.fide_id" in sql and "%(latest)s" in sql


class TestElo:
    def test_band_without_pandas(self):
        assert elo_band_floor(2449) == 2400
        assert elo_band(float("nan")) == "unknown"
        assert elo_band_floor(None) is None

    def test_expected_symmetry(self):
        for d in range(0, 800, 7):
            assert math.isclose(fide_expected(d) + fide_expected(-d), 1.0)


class TestConfig:
    def test_role_fallback(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://a/x")
        monkeypatch.delenv("DATABASE_URL_READER", raising=False)
        assert config.get_database_url("reader") == "postgresql://a/x"
        monkeypatch.setenv("DATABASE_URL_READER", "postgresql://r/x")
        assert config.get_database_url("reader") == "postgresql://r/x"
        assert config.get_database_url() == "postgresql://a/x"

    def test_unknown_role(self):
        with pytest.raises(ValueError):
            config.get_database_url("admin")

    def test_missing_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL_QC", raising=False)
        with pytest.raises(RuntimeError):
            config.get_database_url("qc")
